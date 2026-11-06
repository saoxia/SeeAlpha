from nautilus_trader.common import LogLevel
from nautilus_trader.portfolio import PortfolioConfig

from nautilus_support import instrument_fee_model, result_summary

"""各策略回测入口共享的 NautilusTrader 组装代码。"""

import argparse
import json
import math
from collections.abc import Callable
from dataclasses import dataclass, fields, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from importlib.metadata import version
from pathlib import Path
from uuid import uuid4

from nautilus_trader.adapters.binance import BINANCE_VENUE
from nautilus_trader.backtest import BacktestEngine
from nautilus_trader.config import BacktestEngineConfig, LoggerConfig
from nautilus_trader.model import AccountType, BarType, InstrumentId, Money, OmsType, TraderId

from nautilus_support import Instrument
from pipeline.catalog import ParquetDataCatalog
from pipeline.config import load_settings
from pipeline.models import DataRequest, bar_type_for
from pipeline.prepare_data import prepare_data
from strategy_python._report import backtest_report_dir, write_html_report
from strategy_python._report.report import pnl_data
from strategy_python._strategy import Strategy


@dataclass(frozen=True)
class BacktestSpec:
    """一个策略回测所需的市场和账户设置。"""

    instrument_id: InstrumentId
    data_symbol: str
    bar_type: BarType
    interval: str
    warmup_days: int = 0
    oms_type: OmsType = OmsType.NETTING
    starting_balance: Decimal = Decimal(10000)
    leverage: Decimal = Decimal(3)
    prepare_data_before_run: bool = False


@dataclass(frozen=True)
class StrategyBacktest:
    """单策略回测注册：工厂接收 start、bar_type 及策略特有参数。"""

    name: str
    spec: BacktestSpec
    create_strategy: Callable[..., Strategy]

    def run(
        self,
        start: datetime,
        end: datetime,
        *,
        interval: str | None = None,
        refresh_archives: bool = False,
        force: bool = False,
        check_data: bool | None = None,
        **strategy_parameters: object,
    ) -> Path | None:
        """统一应用运行选项，每次创建新策略并返回报告目录；无数据时返回 None。"""
        if start >= end:
            raise ValueError("开始时间必须早于结束时间")
        spec = with_input_interval(self.spec, interval)
        resolve_data_check(spec, check_data, force=force, refresh_archives=refresh_archives)
        strategy = self.create_strategy(start, bar_type=spec.bar_type, **strategy_parameters)
        return run_backtest(
            start,
            end,
            strategy,
            self.name,
            spec,
            report_dir=backtest_report_dir(self.name),
            refresh_archives=refresh_archives,
            force=force,
            check_data=check_data,
        )


def with_input_interval(spec: BacktestSpec, interval: str | None) -> BacktestSpec:
    """同步回测数据周期与策略订阅类型，策略初始化负责聚合兼容性校验。"""
    if interval is None:
        return spec
    return replace(
        spec,
        interval=interval,
        bar_type=BarType.from_str(bar_type_for(spec.data_symbol, interval)),
    )


def parse_utc_datetime(value: str) -> datetime:
    """把 ISO 日期或时间转换成 UTC 时间。"""
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def default_time_range() -> tuple[datetime, datetime]:
    """返回最近七个完整自然日的 UTC 时间范围。"""
    end = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    return end - timedelta(days=7), end


def build_argument_parser(description: str) -> argparse.ArgumentParser:
    """创建各回测入口共用的命令行参数。"""
    default_start, default_end = default_time_range()
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument(
        "--start",
        type=parse_utc_datetime,
        default=default_start,
        help="UTC 开始时间，例如 2026-08-01",
    )
    parser.add_argument(
        "--end",
        type=parse_utc_datetime,
        default=default_end,
        help="UTC 结束时间，例如 2026-08-08",
    )
    parser.add_argument(
        "--refresh-archives",
        action="store_true",
        help="配合 --check-data，回测前重新检查远端归档 CHECKSUM",
    )
    parser.add_argument("--interval", help="输入 K 线周期，例如 1s、1m、5m；默认沿用策略配置")
    parser.add_argument(
        "--force", action="store_true", help="配合 --check-data，强制重建请求日期的数据"
    )
    parser.add_argument(
        "--check-data",
        action="store_true",
        help="开启归档检查和数据补齐；默认只读取本地已有数据，缺失部分跳过",
    )
    return parser


def run_backtest_cli(
    runner: Callable[..., Path | None],
    parser: argparse.ArgumentParser,
) -> None:
    """统一解析和传递参数；策略自定义参数的 dest 与 runner 参数名保持一致。"""
    args = parser.parse_args()
    try:
        runner(**vars(args))
    except (OSError, ValueError) as error:
        parser.error(str(error))


def resolve_data_check(
    spec: BacktestSpec,
    check_data: bool | None,
    *,
    force: bool,
    refresh_archives: bool,
) -> bool:
    enabled = spec.prepare_data_before_run if check_data is None else check_data
    if not enabled and (force or refresh_archives):
        raise ValueError("--force 和 --refresh-archives 需要同时启用 --check-data")
    return enabled


class BarStream:
    """One-shot bounded batches returned by the native Arrow catalog scanner."""

    def __init__(self, batches):
        self.batches = iter(batches)
        self.first = None
        self.checked = False

    def __bool__(self):
        if not self.checked:
            self.first = next(self.batches, None)
            self.checked = True
        return self.first is not None

    def iter_batches(self):
        if self:
            yield self.first
            self.first = None
        yield from self.batches


def data_batches(data, batch_size=50_000):
    if isinstance(data, BarStream):
        yield from data.iter_batches()
    else:
        for offset in range(0, len(data), batch_size):
            yield data[offset:offset + batch_size]


def load_binance_data(
    start: datetime,
    end: datetime,
    spec: BacktestSpec,
    *,
    refresh_archives: bool = False,
    force: bool = False,
    check_data: bool | None = None,
    streaming: bool = False,
) -> tuple[Instrument | None, list | BarStream]:
    """通过统一数据管道准备并从 Catalog 加载合约和 K 线。"""
    check_data = resolve_data_check(
        spec, check_data, force=force, refresh_archives=refresh_archives
    )
    if check_data:
        dataset = prepare_data(
            DataRequest(
                symbols=(spec.data_symbol,),
                bar_intervals=(spec.interval,),
                start=start,
                end=end,
            ),
            refresh_archives=refresh_archives,
            force=force,
        )
        catalog_path = dataset.catalog_path
    else:
        catalog_path = load_settings().catalog_dir
    catalog = ParquetDataCatalog(str(catalog_path))
    if streaming:
        bars = BarStream(catalog.bar_batches(spec.bar_type, start, end))
    else:
        bars = catalog.bars(bar_types=[str(spec.bar_type)], start=start, end=end)
        start_ns = int(start.timestamp() * 1_000_000_000)
        end_ns = int(end.timestamp() * 1_000_000_000)
        bars = [bar for bar in bars if start_ns <= bar.ts_event < end_ns]
    if not check_data and not bars:
        return None, []
    instruments = catalog.instruments(instrument_ids=[str(spec.instrument_id)])
    if not instruments:
        raise RuntimeError(f"Catalog 中缺少合约定义：{spec.instrument_id}")
    instrument = max(instruments, key=lambda item: item.ts_init)
    return instrument, bars


def run_backtest(
    start: datetime,
    end: datetime,
    strategy: Strategy | list[Strategy],
    strategy_name: str,
    spec: BacktestSpec,
    *,
    report_dir: Path,
    refresh_archives: bool = False,
    force: bool = False,
    check_data: bool | None = None,
    portfolio_parameters: dict | None = None,
) -> Path | None:
    """多个策略共用一个 venue 账户；单策略入口保持兼容。"""
    if start >= end:
        raise ValueError("开始时间必须早于结束时间")

    strategies = strategy if isinstance(strategy, list) else [strategy]
    if not strategies or any(item is None for item in strategies):
        raise ValueError("至少需要一个策略")
    if len(strategies) > 1:
        from strategy_python._execution.portfolio_coordinator import PortfolioCoordinator

        coordinators = [item for item in strategies if isinstance(item, PortfolioCoordinator)]
        if coordinators:
            if spec.oms_type != OmsType.NETTING:
                raise ValueError("净额组合必须使用 NETTING，账户只保留一条净持仓")
            if len(coordinators) != 1:
                raise ValueError("一个组合只能有一个协调器")
        elif spec.oms_type != OmsType.HEDGING:
            raise ValueError("同合约多策略回测必须使用 HEDGING")
        if len({str(item.strategy_id) for item in strategies}) != len(strategies):
            raise ValueError("策略 ID 必须唯一")
        for item in strategies:
            if (
                item.config.instrument_id != spec.instrument_id
                or item.config.bar_type != spec.bar_type
            ):
                raise ValueError("组合策略必须使用同一合约和输入 K 线")

    check_data = resolve_data_check(
        spec, check_data, force=force, refresh_archives=refresh_archives
    )
    spec = replace(spec, prepare_data_before_run=check_data)
    data_start = start - timedelta(days=spec.warmup_days)
    print(
        f"正在{'检查并准备' if check_data else '读取已有'} Binance {spec.interval} 数据："
        f"{data_start.isoformat()} 至 {end.isoformat()}",
    )
    instrument, bars = load_binance_data(
        data_start,
        end,
        spec,
        refresh_archives=refresh_archives,
        force=force,
        check_data=check_data,
        streaming=True,
    )
    if not bars:
        if not check_data:
            print("指定时间范围内没有已有 K 线，跳过本次回测")
            return None
        raise RuntimeError("指定时间范围内没有可用的 K 线数据")
    print(f"采用有界批次读取 {spec.interval} K 线，开始运行 {strategy_name} 策略")

    engine = BacktestEngine(
        config=BacktestEngineConfig(
            trader_id=TraderId("BACKTESTER-001"),
            portfolio=PortfolioConfig(use_mark_prices=False),
            logging=LoggerConfig(stdout_level=LogLevel.ERROR),
        ),
    )
    try:
        engine.add_venue(
            venue=BINANCE_VENUE,
            oms_type=spec.oms_type,
            account_type=AccountType.MARGIN,
            base_currency=None,
            starting_balances=[Money(spec.starting_balance, instrument.quote_currency)],
            default_leverage=spec.leverage,
            fee_model=instrument_fee_model(instrument),
        )
        engine.add_instrument(instrument)
        engine.add_strategies(strategies)
        for batch in data_batches(bars):
            engine.add_data(batch)
            engine.run(streaming=True)
            engine.clear_data()
        engine.end()

        reports = {
            "account": engine.generate_account_report(BINANCE_VENUE),
            "fills": engine.generate_order_fills_report(),
            "positions": engine.generate_positions_report(),
        }
        for name, report in reports.items():
            print(f"\n{name} 报表")
            print(report)
        from strategy_python._execution.portfolio_coordinator import PortfolioCoordinator

        reported = [item for item in strategies if not isinstance(item, PortfolioCoordinator)]
        strategy_parameters = {}
        strategy_statistics = {}
        if len(reported) == 1 and len(strategies) == 1 and portfolio_parameters is None:
            item = reported[0]
            strategy_parameters = {
                "strategy_class": f"{type(item).__module__}.{type(item).__name__}",
                "strategy_config": json.loads(item.config.json()),
            }
        else:
            members = {}
            for index, item in enumerate(reported, start=1):
                prefix = f"strategy_{index:02d}"
                members[str(item.strategy_id)] = {
                    "strategy_class": f"{type(item).__module__}.{type(item).__name__}",
                    "strategy_config": json.loads(item.config.json()),
                    "report_prefix": prefix,
                }
                for name in ("fills", "positions"):
                    frame = reports[name]
                    reports[f"{prefix}_{name}"] = (
                        frame.loc[frame["strategy_id"].astype(str) == str(item.strategy_id)].copy()
                        if "strategy_id" in frame
                        else frame.iloc[:0].copy()
                    )
                data = pnl_data(reports[f"{prefix}_positions"], {"start": start, "end": end})
                strategy_statistics[str(item.strategy_id)] = {
                    "open_positions": data["open_count"],
                    "by_currency": {
                        currency: {
                            "closed_positions": group["count"],
                            "net_pnl": group["points"][-1][3],
                            "win_rate": group["win_rate"],
                            "profit_factor": group["profit_factor"],
                            "realized_pnl_drawdown": group["drawdown"],
                        }
                        for currency, group in data["groups"].items()
                    },
                }
            strategy_parameters = {"strategies": members, "portfolio": portfolio_parameters or {}}
        output = save_backtest_reports(
            report_dir,
            reports,
            parameters={
                "strategy_name": strategy_name,
                **strategy_parameters,
                "backtest_spec": {field.name: getattr(spec, field.name) for field in fields(spec)},
                "start": start.isoformat(),
                "end": end.isoformat(),
                "data_start": data_start.isoformat(),
                "refresh_archives": refresh_archives,
                "force": force,
                "check_data": check_data,
                "nautilus_trader_version": version("nautilus-trader"),
            },
            summary={
                **result_summary(engine.get_result()),
                **({"by_strategy": strategy_statistics} if strategy_statistics else {}),
            },
        )
        print(f"\n回测结果已保存：{output}")
        print(f"中文交互报告：{output / 'report.html'}")
        return output
    finally:
        engine.dispose()


def _json_value(value):
    """没有交易时统计可能为 NaN，以标准 JSON null 表示。"""
    if isinstance(value, dict):
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def save_backtest_reports(
    report_dir: Path, reports: dict, *, parameters: dict, summary: dict
) -> Path:
    """每次运行独立保存，summary.json 最后写入，标志报表导出完成。"""
    run_id = f"{datetime.now(UTC):%Y%m%dT%H%M%S%fZ}_{uuid4().hex}"
    output = report_dir / run_id
    output.mkdir(parents=True, exist_ok=False)
    for name, report in reports.items():
        report.to_csv(output / f"{name}.csv")
    write_html_report(output, reports, parameters=parameters, summary=summary)
    for name, value in (("parameters", parameters), ("summary", summary)):
        temporary = output / f"{name}.json.tmp"
        temporary.write_text(
            json.dumps(
                _json_value(value), ensure_ascii=False, indent=2, default=str, allow_nan=False
            )
            + "\n",
            encoding="utf-8",
        )
        temporary.replace(output / f"{name}.json")
    return output
