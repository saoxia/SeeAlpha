"""读取运行清单，在同一账户中回测任意数量的独立策略。"""

from dataclasses import asdict, replace
from datetime import datetime
from pathlib import Path

from nautilus_trader.model import OmsType

from strategy_python._backtesting import (
    BacktestSpec,
    build_argument_parser,
    resolve_data_check,
    run_backtest_cli,
    with_input_interval,
)
from strategy_python._backtesting import run_backtest as run_engine_backtest
from strategy_python._report import backtest_report_dir
from strategy_python.portfolio import (
    DEFAULT_CONFIG,
    PortfolioConfig,
    create_strategies,
    load_portfolio,
)


def build_backtest_spec(config: PortfolioConfig, *, catalog_only: bool = False) -> BacktestSpec:
    """默认只读本地数据；catalog_only 参数保留给旧调用方兼容。"""
    return BacktestSpec(
        instrument_id=config.instrument_id,
        data_symbol=config.data_symbol,
        bar_type=config.bar_type,
        interval=config.interval,
        warmup_days=config.warmup_days,
        oms_type=OmsType.HEDGING,
        starting_balance=config.starting_balance,
        leverage=config.leverage,
        prepare_data_before_run=False,
    )


def run_backtest(
    start: datetime,
    end: datetime,
    *,
    config_path: Path | str = DEFAULT_CONFIG,
    refresh_archives: bool = False,
    catalog_only: bool = False,
    interval: str | None = None,
    force: bool = False,
    check_data: bool | None = None,
) -> Path | None:
    if start >= end:
        raise ValueError("开始时间必须早于结束时间")
    if catalog_only and (refresh_archives or force):
        raise ValueError("仅使用本地数据时不能刷新远端归档或强制重建")
    if catalog_only:
        if check_data is True:
            raise ValueError("--catalog-only 不能与 --check-data 同时使用")
        check_data = False
    config = load_portfolio(config_path)
    spec = with_input_interval(build_backtest_spec(config, catalog_only=catalog_only), interval)
    resolve_data_check(spec, check_data, force=force, refresh_archives=refresh_archives)
    config = replace(config, interval=spec.interval, bar_type=spec.bar_type)
    strategies, budget = create_strategies(start, config)
    snapshot = {
        "config_path": str(Path(config_path).resolve()),
        "run_config": asdict(config),
        "risk_mode": config.risk_mode,
        "members": {
            str(strategy.strategy_id): member.id
            for strategy, member in zip(strategies, config.active_members)
        },
        **(budget.snapshot() if budget else {}),
    }
    return run_engine_backtest(
        start,
        end,
        strategies,
        config.name,
        spec,
        report_dir=backtest_report_dir(config.name, portfolio=True),
        refresh_archives=refresh_archives,
        force=force,
        check_data=check_data,
        portfolio_parameters=snapshot,
    )


def main() -> None:
    parser = build_argument_parser("根据配置清单运行多策略共用账户回测")
    parser.add_argument(
        "--config",
        dest="config_path",
        type=Path,
        default=DEFAULT_CONFIG,
        help="运行清单 JSON 路径，默认均值回归多空配置",
    )
    parser.add_argument("--catalog-only", action="store_true", help="仅使用已有本地行情，不下载")
    run_backtest_cli(run_backtest, parser)


if __name__ == "__main__":
    main()
