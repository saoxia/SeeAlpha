"""Single-symbol dual EMA crossover. It publishes a target holding and does not order.

Golden cross (fast EMA rises through the slow EMA) requests the full long allocation.
Death cross requests a flat position. Both decisions stay inside this strategy.
"""

from dataclasses import dataclass
from decimal import Decimal

from nautilus_trader.indicators import ExponentialMovingAverage
from nautilus_trader.trading import Strategy, StrategyConfig

from engine.strategies.targets import TargetHolding


@dataclass(frozen=True)
class EmaSpec:
    symbol: str
    instrument_id: object
    bar_type: object
    fast_period: int
    slow_period: int
    allocation: Decimal


class EmaCrossover(Strategy):
    """Long the full allocation after a golden cross, otherwise flat."""

    def __init__(self, config: StrategyConfig) -> None:
        # Nautilus constructs the strategy with the native config only.
        super().__init__(config)
        self.spec: EmaSpec | None = None
        self.fast = None
        self.slow = None
        self.published: list[TargetHolding] = []
        self._previous: tuple[float, float] | None = None
        self._quantity = Decimal(0)
        self.target_sink = None

    def on_start(self) -> None:
        self.fast = ExponentialMovingAverage(self.spec.fast_period)
        self.slow = ExponentialMovingAverage(self.spec.slow_period)
        self.register_indicator_for_bars(self.spec.bar_type, self.fast)
        self.register_indicator_for_bars(self.spec.bar_type, self.slow)
        self.subscribe_bars(self.spec.bar_type)

    def on_bar(self, bar) -> None:
        if bar.bar_type != self.spec.bar_type:
            return
        if not self.fast.initialized or not self.slow.initialized:
            return
        fast = float(self.fast.value)
        slow = float(self.slow.value)
        previous = self._previous
        self._previous = (fast, slow)
        if previous is None:
            return
        previous_fast, previous_slow = previous
        if previous_fast <= previous_slow and fast > slow:
            self._quantity = self.spec.allocation
        elif previous_fast >= previous_slow and fast < slow:
            self._quantity = Decimal(0)
        else:
            return
        holding = TargetHolding(symbol=self.spec.symbol, quantity=self._quantity)
        self.published.append(holding)
        if self.target_sink is not None:
            self.target_sink(self, holding)
