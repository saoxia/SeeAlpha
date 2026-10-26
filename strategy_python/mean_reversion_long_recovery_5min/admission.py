"""Causal reclaim state and a conservative, price-rounded cost admission rule."""

from dataclasses import dataclass
from decimal import ROUND_CEILING, Decimal
from math import isfinite

from strategy_python._indicator.bars import NANOSECONDS_PER_MINUTE

FIVE_MINUTES = 5 * NANOSECONDS_PER_MINUTE


@dataclass(frozen=True)
class ReclaimQuote:
    bands: tuple[float, float, float]
    created_ns: int
    expires_ns: int
    high: float


class ReclaimAdmission:
    """A close below a prior band followed immediately by a close back above it."""

    def __init__(self):
        self.reset()

    def reset(self):
        self.last_end = None
        self.breach_bands = None
        self.quote = None

    def update(self, bar, prior_bands):
        if self.last_end is not None and bar.ts_end <= self.last_end:
            raise ValueError("Completed five-minute bars must increase")
        continuous = self.last_end is not None and bar.ts_end == self.last_end + FIVE_MINUTES
        self.quote = None
        if continuous and self.breach_bands is not None:
            lower, middle, _ = self.breach_bands
            if lower < bar.close < middle:
                self.quote = ReclaimQuote(
                    self.breach_bands, bar.ts_end, bar.ts_end + FIVE_MINUTES, bar.high
                )
        self.breach_bands = None
        if continuous and prior_bands is not None and all(isfinite(x) for x in prior_bands):
            lower, middle, upper = prior_bands
            if 0 < lower < middle < upper and bar.close < lower:
                self.breach_bands = prior_bands
        self.last_end = bar.ts_end
        return self.quote

    def current(self, now_ns):
        quote = self.quote
        return quote if quote and quote.created_ns <= now_ns < quote.expires_ns else None


def recovery_price(quote, close, now_ns, tick, allowance_bp):
    """A later closed minute must exceed the confirmation high before a capped entry."""
    if close is None:
        return None
    close, high, middle, tick, allowance = (
        Decimal(str(x)) for x in (close, quote.high, quote.bands[1], tick, allowance_bp)
    )
    if any(not x.is_finite() for x in (close, high, middle, tick, allowance)):
        return None
    if min(high, tick) <= 0 or allowance < 0 or close <= high:
        return None
    if not quote.created_ns < now_ns < quote.expires_ns:
        return None
    limit = (close * (1 + allowance / 10000) / tick).to_integral_value(
        rounding=ROUND_CEILING
    ) * tick
    if limit >= middle:
        return None
    return limit


def reward_cost_allowed(entry, target, fee, reserve_bp, minimum_ratio):
    """Per-entry-notional cost includes entry and exit notionals, not just 2*fee."""
    values = tuple(Decimal(str(x)) for x in (entry, target, fee, reserve_bp, minimum_ratio))
    if any(not x.is_finite() for x in values):
        return False
    entry, target, fee, reserve_bp, ratio = values
    if entry <= 0 or target <= entry or fee < 0 or reserve_bp < 0 or ratio < 1:
        return False
    gross = target / entry - 1
    per_side = fee + reserve_bp / Decimal(10000)
    cost = per_side * (1 + target / entry)
    return gross >= ratio * cost
