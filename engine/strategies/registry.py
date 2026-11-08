"""Allowlist of strategy packages the engine can instantiate.

The business store may catalog only these code keys. strategy_python is not a source.
"""

from decimal import Decimal, InvalidOperation

from nautilus_trader.trading import StrategyConfig

from engine.strategies.ema_crossover import EmaCrossover, EmaSpec


class ParameterError(ValueError):
    """A package parameter is missing or outside the package contract."""


DEFAULTS = {
    "symbol": "ETHUSDT",
    "fast_period": 8,
    "slow_period": 21,
    "allocation": "1",
    "risk_limit": "1",
}
SECRET_FIELDS = {"api_key", "secret", "passphrase", "password", "token", "credential", "credentials"}


def require_package(code_key):
    if isinstance(code_key, str) and "strategy_python" in code_key:
        raise ParameterError("不能从 strategy_python 加载策略。")
    if code_key not in PACKAGES:
        raise ParameterError("策略包不在引擎可加载清单中。")
    return PACKAGES[code_key]


def validate_ema(parameters):
    if not isinstance(parameters, dict):
        raise ParameterError("策略参数无效。")
    unknown = set(parameters) - set(DEFAULTS)
    if unknown & SECRET_FIELDS:
        raise ParameterError("策略参数不能包含密钥。")
    if unknown:
        raise ParameterError("策略参数包含未知字段。")
    merged = {**DEFAULTS, **parameters}
    if "risk_limit" not in parameters:
        merged["risk_limit"] = merged["allocation"]
    symbol = str(merged["symbol"]).upper()
    if symbol != "ETHUSDT":
        raise ParameterError("双均线示例目前只交易 ETHUSDT。")
    fast = _period(merged["fast_period"], "快均线周期")
    slow = _period(merged["slow_period"], "慢均线周期")
    if fast >= slow:
        raise ParameterError("快均线周期必须小于慢均线。")
    allocation = _quantity(merged["allocation"], "分配数量")
    risk_limit = _quantity(merged["risk_limit"], "风险额度")
    return {
        "symbol": symbol,
        "fast_period": fast,
        "slow_period": slow,
        "allocation": allocation,
        "risk_limit": risk_limit,
    }


def validate(code_key, parameters):
    package = require_package(code_key)
    return package["validate"](parameters or {})


def build(code_key, parameters, instrument, bar_type, order_id_tag):
    package = require_package(code_key)
    return package["build"](parameters, instrument, bar_type, order_id_tag)


def _build_ema(parameters, instrument, bar_type, order_id_tag):
    strategy = EmaCrossover(StrategyConfig(order_id_tag=order_id_tag))
    strategy.spec = EmaSpec(
        symbol=parameters["symbol"],
        instrument_id=instrument.id,
        bar_type=bar_type,
        fast_period=parameters["fast_period"],
        slow_period=parameters["slow_period"],
        allocation=Decimal(parameters["allocation"]),
    )
    return strategy


def _period(value, label):
    if isinstance(value, (bool, float)):
        raise ParameterError(f"{label}必须是整数。")
    if isinstance(value, str) and value.isdigit():
        value = int(value)
    if not isinstance(value, int) or isinstance(value, bool) or not 2 <= value <= 500:
        raise ParameterError(f"{label}必须是 2 到 500 的整数。")
    return value


def _quantity(value, label):
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ParameterError(f"{label}必须是大于零的十进制数。") from None
    digits = amount.as_tuple()
    if (
        isinstance(value, bool)
        or not amount.is_finite()
        or amount <= 0
        or digits.exponent < -8
        or len(digits.digits) > 30
    ):
        raise ParameterError(f"{label}必须是大于零的十进制数。")
    return format(amount, "f")


PACKAGES = {
    "ema_crossover": {"validate": validate_ema, "build": _build_ema},
}
