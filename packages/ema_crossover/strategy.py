"""Dual EMA crossover. Golden cross publishes a full long target; death cross publishes flat.

Market bars enter ``on_bar``. This module owns the two Nautilus EMAs and the
cross decision. The only value it hands to SeeAlpha is ``publish(...)``.
"""

from decimal import Decimal, InvalidOperation

from nautilus_trader.indicators import ExponentialMovingAverage
from nautilus_trader.trading import Strategy

DEFAULTS = {
    "symbol": "ETHUSDT",
    "fast_period": 8,
    "slow_period": 21,
    "allocation": "1",
    "risk_limit": "1",
}


def prepare(parameters):
    """Normalize issued parameters. Unknown fields and secrets are rejected by the engine."""
    if not isinstance(parameters, dict):
        raise TypeError("策略参数无效。")
    unknown = set(parameters) - set(DEFAULTS)
    if unknown:
        raise ValueError("策略参数包含未知字段。")
    merged = {**DEFAULTS, **parameters}
    if "risk_limit" not in parameters:
        merged["risk_limit"] = merged["allocation"]
    symbol = str(merged["symbol"]).upper()
    if symbol != "ETHUSDT":
        raise ValueError("双均线示例目前只交易 ETHUSDT。")
    fast = _period(merged["fast_period"], "快均线周期")
    slow = _period(merged["slow_period"], "慢均线周期")
    if fast >= slow:
        raise ValueError("快均线周期必须小于慢均线。")
    allocation = _quantity(merged["allocation"], "分配数量")
    risk_limit = _quantity(merged["risk_limit"], "风险额度")
    return {
        "symbol": symbol,
        "fast_period": fast,
        "slow_period": slow,
        "allocation": allocation,
        "risk_limit": risk_limit,
    }


def _period(value, label):
    if isinstance(value, bool):
        raise TypeError(f"{label}必须是整数。")
    if isinstance(value, str) and value.isdigit():
        value = int(value)
    if not isinstance(value, int) or not 2 <= value <= 500:
        raise ValueError(f"{label}必须是 2 到 500 的整数。")
    return value


def _quantity(value, label):
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError(f"{label}必须是大于零的十进制数。") from None
    digits = amount.as_tuple()
    if (
        isinstance(value, bool)
        or not amount.is_finite()
        or amount <= 0
        or digits.exponent < -8
        or len(digits.digits) > 30
    ):
        raise ValueError(f"{label}必须是大于零的十进制数。")
    return format(amount, "f")


class EmaCrossover(Strategy):
    """Long the full allocation after a golden cross, otherwise flat."""

    def __init__(self, config) -> None:
        super().__init__(config)
        self.fast = None
        self.slow = None
        self._previous = None
        self._quantity = Decimal(0)

    def on_start(self) -> None:
        self.fast = ExponentialMovingAverage(self.parameters["fast_period"])
        self.slow = ExponentialMovingAverage(self.parameters["slow_period"])
        self.register_indicator_for_bars(self.bar_type, self.fast)
        self.register_indicator_for_bars(self.bar_type, self.slow)
        self.subscribe_bars(self.bar_type)

    def on_bar(self, bar) -> None:
        if bar.bar_type != self.bar_type or not self.fast.initialized or not self.slow.initialized:
            return
        fast = float(self.fast.value)
        slow = float(self.slow.value)
        previous = self._previous
        self._previous = (fast, slow)
        if previous is None:
            return
        previous_fast, previous_slow = previous
        if previous_fast <= previous_slow and fast > slow:
            self._quantity = Decimal(self.parameters["allocation"])
        elif previous_fast >= previous_slow and fast < slow:
            self._quantity = Decimal(0)
        else:
            return
        self.publish(symbol=self.parameters["symbol"], quantity=self._quantity)
