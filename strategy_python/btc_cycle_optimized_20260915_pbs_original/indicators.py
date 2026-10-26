"""已收盘日线 BOLL 和小时均线；断档时重建对应窗口。"""

from collections import deque
from dataclasses import dataclass

from nautilus_trader.indicators import (
    AverageTrueRange,
    BollingerBands,
    DonchianChannel,
    MovingAverageType,
    SimpleMovingAverage,
)

from strategy_python._indicator.bars import CompletedBar

DAY_NS = 86400_000_000_000


@dataclass(frozen=True)
class Features:
    close: float
    previous_close: float
    previous_ma: float
    ma: float
    atr: float
    swing_high: float
    down_daily: bool


class PullbackIndicators:
    def __init__(
        self,
        *,
        ma_period=20,
        daily_boll_period=20,
        daily_boll_std=2.0,
        atr_period=14,
        swing_period=12,
    ):
        self.ma_period = ma_period
        self.daily_boll_period = daily_boll_period
        self.daily_boll_std = daily_boll_std
        self.atr_period = atr_period
        self.swing_period = swing_period
        self.reset()

    def reset(self):
        self._daily = BollingerBands(self.daily_boll_period, self.daily_boll_std, MovingAverageType.Simple)
        self._mean = SimpleMovingAverage(self.ma_period)
        self._tr = AverageTrueRange(1, MovingAverageType.Simple, use_previous=True)
        self._atr = SimpleMovingAverage(self.atr_period)
        self._swing = DonchianChannel(self.swing_period)
        self.day_closes = deque(maxlen=self.daily_boll_period)
        self.bars = deque(maxlen=max(self.ma_period, self.swing_period))
        self.ranges = deque(maxlen=self.atr_period)
        self.last_day = self.last_hour = None
        self.down_daily = False
        self.previous = None

    def update_daily(self, bar: CompletedBar):
        if self.last_day is not None and bar.ts_start != self.last_day:
            self.day_closes.clear()
            self._daily.reset()
        self.last_day = bar.ts_end
        self.day_closes.append(bar.close)
        self._daily.update_raw(bar.close)
        self.down_daily = False
        if len(self.day_closes) < self.daily_boll_period:
            return
        # [统计定义] 完整日线收盘SMA及总体标准差；仅用当时已完成的日线。
        # low穿轨不是收盘正态尾概率，20/2仍是背景规则的固定选择。
        self.down_daily = bar.close < self._daily.middle and bar.low < self._daily.lower

    def update_hour(self, bar: CompletedBar) -> Features | None:
        if self.last_hour is not None and bar.ts_start != self.last_hour:
            self.bars.clear()
            self._mean.reset()
            self._tr.reset()
            self._atr.reset()
            self._swing.reset()
            self.ranges.clear()
            self.previous = None
        self.last_hour = bar.ts_end
        self._mean.update_raw(bar.close)
        self._swing.update_raw(bar.high, bar.low)
        self._tr.update_raw(bar.high, bar.low, bar.close)
        if self.bars:
            self._atr.update_raw(self._tr.value)
            self.ranges.append(self._tr.value)
        self.bars.append(bar)
        if (
            len(self.bars) < max(self.ma_period, self.swing_period)
            or len(self.ranges) < self.atr_period
        ):
            return None
        mean = self._mean.value
        previous = self.previous
        self.previous = (bar.close, mean)
        if previous is None:
            return None
        # Current UTC day's regime must come from the day closed at its midnight.
        # If that entire daily bar is missing/incomplete, the old regime expires.
        valid_day = self.last_day == bar.ts_end // DAY_NS * DAY_NS
        return Features(
            bar.close,
            *previous,
            mean,
            self._atr.value,
            self._swing.upper,
            self.down_daily and valid_day,
        )
