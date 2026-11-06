"""从运行清单创建策略，并在多成员净额模式下绑定组合协调器。"""

import json
import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from nautilus_trader.model import BarType, InstrumentId

from pipeline.models import bar_type_for
from strategy_python._execution.portfolio_coordinator import (
    PortfolioCoordinator,
    PortfolioCoordinatorConfig,
)
from strategy_python._risk.shared_budget import SharedRiskBudget
from strategy_python.mean_reversion_long_5min import (
    MeanReversionLong5MinConfig,
    MeanReversionLong5MinStrategy,
)
from strategy_python.mean_reversion_short_5min import (
    MeanReversionShort5MinConfig,
    MeanReversionShort5MinStrategy,
)
from strategy_python.mean_reversion_short_v2_5min import (
    MeanReversionShortV2Config,
    MeanReversionShortV2Strategy,
)
from strategy_python.pullback_short_1h import (
    PullbackShort1hConfig,
    PullbackShort1hStrategy,
)
from strategy_python.trend_following_long_5min import (
    TrendFollowingLong5MinConfig,
    TrendFollowingLong5MinStrategy,
)
from strategy_python.trend_following_short_5min import (
    TrendFollowingShort5MinConfig,
    TrendFollowingShort5MinStrategy,
)

DEFAULT_CONFIG = Path(__file__).with_name("_portfolio") / "mean_reversion.json"
INSTRUMENT_ID = InstrumentId.from_str("ETHUSDT-PERP.BINANCE")
BAR_TYPE = BarType.from_str("ETHUSDT-PERP.BINANCE-1-MINUTE-LAST-EXTERNAL")
# 新策略只需在此注册配置类和策略类，运行入口不随组合成员变化。
STRATEGY_REGISTRY = {
    "mean_reversion_short_v2_5min": (MeanReversionShortV2Config, MeanReversionShortV2Strategy),
    "pullback_short_1h": (PullbackShort1hConfig, PullbackShort1hStrategy),
    "mean_reversion_long_5min": (MeanReversionLong5MinConfig, MeanReversionLong5MinStrategy),
    "mean_reversion_short_5min": (MeanReversionShort5MinConfig, MeanReversionShort5MinStrategy),
    "trend_following_long_5min": (TrendFollowingLong5MinConfig, TrendFollowingLong5MinStrategy),
    "trend_following_short_5min": (TrendFollowingShort5MinConfig, TrendFollowingShort5MinStrategy),
}
RESERVED_PARAMETERS = {"instrument_id", "bar_type", "trade_start_ns", "strategy_id", "order_id_tag"}


@dataclass(frozen=True)
class StrategyMember:
    id: str
    strategy: str
    enabled: bool
    risk_weight: Decimal | None
    parameters: dict


@dataclass(frozen=True)
class PortfolioConfig:
    name: str
    instrument_id: InstrumentId
    bar_type: BarType
    data_symbol: str
    interval: str
    starting_balance: Decimal
    leverage: Decimal
    warmup_days: int
    risk_mode: str
    total_risk_fraction: Decimal | None
    members: tuple[StrategyMember, ...]
    execution: str = "netted"

    @property
    def active_members(self) -> tuple[StrategyMember, ...]:
        return tuple(member for member in self.members if member.enabled)


def _keys(value: object, allowed: set[str], required: set[str], label: str) -> dict:
    if not isinstance(value, dict):
        raise ValueError(f"{label} 必须是对象")  # noqa: TRY004 - 统一报告配置错误
    if value.keys() - allowed or required - value.keys():
        raise ValueError(
            f"{label} 字段错误：未知 {sorted(value.keys() - allowed)}；缺少 {sorted(required - value.keys())}"
        )
    return value


def _positive(value: object, label: str) -> Decimal:
    try:
        number = Decimal(str(value))
    except InvalidOperation as error:
        raise ValueError(f"{label} 必须为有限正数") from error
    if not number.is_finite() or number <= 0:
        raise ValueError(f"{label} 必须为有限正数")
    return number


def parse_portfolio(value: object) -> PortfolioConfig:
    root = _keys(
        value,
        {"name", "market", "account", "backtest", "risk", "strategies", "execution"},
        {"name", "market", "account", "backtest", "risk", "strategies"},
        "运行清单",
    )
    execution = root.get("execution", "netted")
    if execution not in ("netted", "independent"):
        raise ValueError("execution 必须为 netted 或 independent")
    name = root["name"]
    if not isinstance(name, str) or not re.fullmatch(r"[a-zA-Z0-9_]+", name):
        raise ValueError("组合名称只能包含字母、数字和下划线")
    market = _keys(
        root["market"],
        {"instrument_id", "bar_type", "data_symbol", "interval"},
        {"instrument_id", "bar_type", "data_symbol", "interval"},
        "market",
    )
    try:
        instrument = InstrumentId.from_str(market["instrument_id"])
        bar_type = BarType.from_str(market["bar_type"])
    except (TypeError, ValueError) as error:
        raise ValueError("合约或 K 线类型格式错误") from error
    if str(instrument.venue) != "BINANCE" or bar_type.instrument_id != instrument:
        raise ValueError("当前入口只支持 Binance，且合约与 K 线合约必须一致")
    if not isinstance(market["data_symbol"], str) or not re.fullmatch(
        r"[A-Z0-9]+", market["data_symbol"]
    ):
        raise ValueError("data_symbol 必须为大写行情标的")
    if str(instrument.symbol) != market["data_symbol"] + "-PERP":
        raise ValueError("data_symbol 必须对应永续合约")
    if str(bar_type) != bar_type_for(market["data_symbol"], market["interval"]):
        raise ValueError("interval 必须与 bar_type 一致")
    account = _keys(
        root["account"],
        {"starting_balance", "leverage"},
        {"starting_balance", "leverage"},
        "account",
    )
    backtest = _keys(root["backtest"], {"warmup_days"}, {"warmup_days"}, "backtest")
    warmup = backtest["warmup_days"]
    if type(warmup) is not int or warmup < 0:
        raise ValueError("warmup_days 必须为非负整数")
    risk = _keys(root["risk"], {"mode", "total_fraction"}, {"mode"}, "risk")
    if risk["mode"] not in ("shared", "none"):
        raise ValueError("risk.mode 必须为 shared 或 none")
    total = None
    if risk["mode"] == "shared":
        total = _positive(risk.get("total_fraction"), "总风险比例")
        if total > 1:
            raise ValueError("总风险比例不能大于 1")
    elif "total_fraction" in risk:
        raise ValueError("none 模式不得声明总风险比例")
    if not isinstance(root["strategies"], list):
        raise ValueError("strategies 必须是列表")  # noqa: TRY004 - 统一报告配置错误
    members = []
    identifiers = set()
    for entry in root["strategies"]:
        item = _keys(
            entry,
            {"id", "strategy", "enabled", "risk_weight", "parameters"},
            {"id", "strategy"},
            "策略成员",
        )
        key = item["id"]
        if (
            not isinstance(key, str)
            or not re.fullmatch(r"[A-Za-z0-9_]+", key)
            or key in identifiers
        ):
            raise ValueError("成员 id 必须唯一，且只能包含字母、数字和下划线")
        identifiers.add(key)
        if not isinstance(item["strategy"], str) or item["strategy"] not in STRATEGY_REGISTRY:
            raise ValueError(f"未注册的策略：{item['strategy']}")
        enabled = item.get("enabled", True)
        if type(enabled) is not bool:
            raise ValueError("enabled 必须为布尔值")
        parameters = item.get("parameters", {})
        if not isinstance(parameters, dict) or parameters.keys() & RESERVED_PARAMETERS:
            raise ValueError("parameters 必须为对象，且不能覆盖合约、输入周期、交易起点或策略编号")
        weight = _positive(item["risk_weight"], "风险权重") if "risk_weight" in item else None
        if risk["mode"] == "shared" and enabled and weight is None:
            raise ValueError("启用的策略必须提供 risk_weight")
        if risk["mode"] == "none" and weight is not None:
            raise ValueError("none 模式不得声明风险权重")
        members.append(StrategyMember(key, item["strategy"], enabled, weight, dict(parameters)))
    active = [member for member in members if member.enabled]
    if not active:
        raise ValueError("至少启用一个策略")
    if total is not None:
        SharedRiskBudget(total, {member.id: member.risk_weight for member in active})
    return PortfolioConfig(
        name,
        instrument,
        bar_type,
        market["data_symbol"],
        market["interval"],
        _positive(account["starting_balance"], "初始资金"),
        _positive(account["leverage"], "杠杆"),
        warmup,
        risk["mode"],
        total,
        tuple(members),
        execution,
    )


def load_portfolio(path: Path | str = DEFAULT_CONFIG) -> PortfolioConfig:
    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"JSON 字段重复：{key}")
            result[key] = value
        return result

    return parse_portfolio(
        json.loads(Path(path).read_text(encoding="utf-8"), object_pairs_hook=unique_object)
    )


def create_strategies(
    start: datetime, config: PortfolioConfig | None = None
) -> tuple[list, SharedRiskBudget | None]:
    config = config or load_portfolio()
    budget = (
        SharedRiskBudget(
            config.total_risk_fraction, {m.id: m.risk_weight for m in config.active_members}
        )
        if config.risk_mode == "shared"
        else None
    )
    strategies = []
    for member in config.active_members:
        config_class, strategy_class = STRATEGY_REGISTRY[member.strategy]
        if budget is not None and not callable(
            getattr(strategy_class, "attach_shared_risk_budget", None)
        ):
            raise ValueError(
                f"策略 {member.id} 尚未接入共享风险预算；请使用 risk.mode=none 或先完成风控适配"
            )
        try:
            unknown = member.parameters.keys() - set(config_class.parameter_names)
            if unknown:
                raise ValueError(f"未知参数：{sorted(unknown)}")
            raw = {
                **member.parameters,
                "instrument_id": str(config.instrument_id),
                "bar_type": str(config.bar_type),
                "trade_start_ns": int(start.timestamp() * 1_000_000_000),
                "order_id_tag": member.id,
            }
            strategy = strategy_class(config_class.parse(json.dumps(raw, allow_nan=False)))
        except (TypeError, ValueError) as error:
            raise ValueError(f"策略 {member.id} 配置错误：{error}") from error
        if budget is not None:
            strategy.attach_shared_risk_budget(budget, member.id)
        strategies.append(strategy)
    if config.execution == "netted" and len(strategies) > 1:
        coordinator = PortfolioCoordinator(
            PortfolioCoordinatorConfig(
                instrument_id=config.instrument_id,
                bar_type=config.bar_type,
                order_id_tag="PORTFOLIO_NET",
            ),
        )
        for strategy in strategies:
            strategy.attach_portfolio_coordinator(coordinator)
    return strategies, budget


def uses_net_execution(strategies: list) -> bool:
    """协调器已绑定且会被注册到引擎时，账户改为净持仓并由协调器发单。"""
    return bool(strategies) and getattr(strategies[0], "_portfolio_coordinator", None) is not None


def with_net_execution(strategies: list) -> list:
    """协调器最先注册。净额在本根 K 线的警报里计算，不依赖这个顺序。"""
    if not uses_net_execution(strategies):
        return strategies
    return [strategies[0]._portfolio_coordinator, *strategies]
