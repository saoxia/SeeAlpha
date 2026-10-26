"""Causal daily trend gate; no account, order, or future-price access."""

from collections import deque
from math import isfinite

from strategy_python._indicator.bars import NANOSECONDS_PER_DAY


class DailyRegime:
    def __init__(self, period: int = 52, slope_days: int = 7):
        if period < 2 or slope_days < 1:
            raise ValueError("Daily regime needs period>=2 and slope_days>=1")
        self.period, self.slope_days = period, slope_days
        self.closes = deque(maxlen=period + slope_days)
        self.last_end = None
        self.allowed = False

    def reset(self):
        self.closes.clear()
        self.last_end = None
        self.allowed = False

    def update(self, end_ns, close):
        if not isfinite(close) or close <= 0 or end_ns % NANOSECONDS_PER_DAY:
            raise ValueError("Expected a completed UTC daily bar")
        if self.last_end is not None and end_ns <= self.last_end:
            raise ValueError("Daily bars must increase")
        if self.last_end is not None and end_ns != self.last_end + NANOSECONDS_PER_DAY:
            self.reset()
        self.last_end = end_ns
        self.closes.append(close)
        self.allowed = False
        if len(self.closes) == self.closes.maxlen:
            values = list(self.closes)
            current = sum(values[-self.period :]) / self.period
            previous = sum(values[: self.period]) / self.period
            self.allowed = close > current > previous
        return self.allowed

    def allows_entry(self, now_ns):
        return (
            self.allowed
            and self.last_end is not None
            and 0 <= now_ns - self.last_end <= NANOSECONDS_PER_DAY
        )
