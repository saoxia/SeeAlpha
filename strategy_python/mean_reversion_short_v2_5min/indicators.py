"""连续完整五分钟 BOLL/ATR；已收盘小时线提供可选背景过滤。"""

from collections import deque
from dataclasses import dataclass

from nautilus_trader.indicators import (
    AverageTrueRange,
    BollingerBands,
    DonchianChannel,
    EfficiencyRatio,
    MovingAverageType,
    SimpleMovingAverage,
)

from strategy_python._indicator.bars import CompletedBar

HOUR_NS = 3600_000_000_000


@dataclass(frozen=True)
class Features:
    open: float
    high: float
    close: float
    ma: float
    std: float
    atr: float
    previous_close: float
    previous_upper: float
    flat: bool
    down: bool


class MeanReversionIndicators:
    def __init__(self, period=20, std_multiplier=3.0, atr_period=14):
        self.period = period
        self.std_multiplier = std_multiplier
        self.atr_period = atr_period
        self.reset()

    def reset(self):
        self._bands = BollingerBands(self.period, self.std_multiplier, MovingAverageType.Simple)
        self._tr = AverageTrueRange(1, MovingAverageType.Simple, use_previous=True)
        self._atr = SimpleMovingAverage(self.atr_period)
        self._hour_mean = SimpleMovingAverage(20)
        self._efficiency = EfficiencyRatio(24)
        self._hour_range = DonchianChannel(25)
        self.closes = deque(maxlen=self.period)
        self.ranges = deque(maxlen=self.atr_period)
        self.hours = deque(maxlen=25)
        self.hour_means = deque(maxlen=4)
        self.last_hour = self.last_five = None
        self.previous = None
        self.flat = self.down = False

    def update_hour(self, bar: CompletedBar):
        if self.last_hour is not None and bar.ts_start != self.last_hour:
            self.hours.clear()
            self._hour_mean.reset()
            self._efficiency.reset()
            self._hour_range.reset()
            self.hour_means.clear()
        self.last_hour = bar.ts_end
        self.hours.append(bar.close)
        self._hour_mean.update_raw(bar.close)
        self._efficiency.update_raw(bar.close)
        self._hour_range.update_raw(bar.close, bar.close)
        self.flat = self.down = False
        if len(self.hours) >= 20:
            mean = self._hour_mean.value
            self.hour_means.append(mean)
            if len(self.hour_means) == 4:
                self.down = bar.close <= mean <= self.hour_means[0]
        if len(self.hours) == 25:
            self.flat = self._hour_range.upper > self._hour_range.lower and self._efficiency.value < 0.35

    def update_five(self, bar: CompletedBar) -> Features | None:
        if self.last_five is not None and bar.ts_start != self.last_five:
            self.closes.clear()
            self._bands.reset()
            self._tr.reset()
            self._atr.reset()
            self.ranges.clear()
            self.previous = None
        self.last_five = bar.ts_end
        self._tr.update_raw(bar.high, bar.low, bar.close)
        self._bands.update_raw(bar.close)
        if self.closes:
            self._atr.update_raw(self._tr.value)
            self.ranges.append(self._tr.value)
        self.closes.append(bar.close)
        if len(self.closes) < self.period or len(self.ranges) < self.atr_period:
            return None
        ma, std = self._bands.middle, self._bands.stddev
        previous = self.previous
        self.previous = (bar.close, ma + self.std_multiplier * std)
        if previous is None:
            return None
        fresh = self.last_hour == bar.ts_end // HOUR_NS * HOUR_NS
        return Features(
            bar.open,
            bar.high,
            bar.close,
            ma,
            std,
            self._atr.value,
            *previous,
            self.flat and fresh,
            self.down and fresh,
        )
