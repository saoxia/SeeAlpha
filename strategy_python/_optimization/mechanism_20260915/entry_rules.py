"""Pure, entry-time rules for the declared mechanism candidate (not yet wired).

Call only with completed, currently observable bars. Returned values are frozen
snapshots; a running trade must not replace them when later volatility changes.
"""

from collections import deque
from dataclasses import dataclass
from decimal import Decimal
from math import isfinite


class HourlyATR:
    def __init__(self, period=14):
        self.period = period
        self.reset()

    def reset(self):
        self.ranges = deque(maxlen=self.period)
        self.previous_close = None
        self.previous_end = None

    def update(self, end_ns, high, low, close):
        if self.previous_end is not None and end_ns != self.previous_end + 3600_000_000_000:
            self.reset()
        if self.previous_close is not None:
            self.ranges.append(
                max(high - low, abs(high - self.previous_close), abs(low - self.previous_close))
            )
        self.previous_close, self.previous_end = close, end_ns

    @property
    def value(self):
        return sum(self.ranges) / self.period if len(self.ranges) == self.period else None


@dataclass(frozen=True)
class TrendSnapshot:
    stop_return: float
    activation_return: float
    notional_fraction: float


def trend_snapshot(
    price, atr, stop_multiple, activation_multiple, risk_fraction=0.005, notional_cap=1.0
):
    values = (price, atr, stop_multiple, activation_multiple, risk_fraction, notional_cap)
    if any(value is None or not isfinite(value) or value <= 0 for value in values):
        return None
    stop = stop_multiple * atr / price
    if stop >= 1:
        return None
    return TrendSnapshot(
        stop, activation_multiple * atr / price, min(notional_cap, risk_fraction / stop)
    )


@dataclass(frozen=True)
class MeanReversionSnapshot:
    take_profit_return: float
    add_offsets: tuple[tuple[int, float], ...]


def mean_reversion_snapshot(lower, middle, multipliers, fee_floor=0.0004):
    if not 0 < lower < middle or len(multipliers) != 3:
        return None
    if any(not isfinite(x) or x < 0 for x in multipliers) or tuple(multipliers) != tuple(
        sorted(multipliers)
    ):
        return None
    gap = middle / lower - 1
    offsets = tuple((i + 2, max(fee_floor, value * gap)) for i, value in enumerate(multipliers))
    # Avoid nonpositive future order prices in extreme/invalid data.
    if any(distance >= 1 for _, distance in offsets):
        return None
    return MeanReversionSnapshot(gap, offsets)


def pullback_stop(price, swing_high, atr, tick, buffer_multiple, minimum_atr, maximum_atr):
    if (
        min(price, swing_high, atr, tick, minimum_atr) <= 0
        or not 0 <= buffer_multiple
        or maximum_atr <= minimum_atr
    ):
        return None
    # At least one tradable tick beyond the structural high.
    stop_decimal = Decimal(str(swing_high)) + max(
        Decimal(str(tick)), Decimal(str(buffer_multiple)) * Decimal(str(atr))
    )
    stop = float(stop_decimal)
    distance_atr = (stop - price) / atr
    return stop if minimum_atr <= distance_atr <= maximum_atr else None
