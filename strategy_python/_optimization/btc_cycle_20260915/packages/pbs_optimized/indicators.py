"""已收盘日线 BOLL 和小时均线；断档时重建对应窗口。"""

from collections import deque
from dataclasses import dataclass
from math import sqrt

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
        self.day_closes = deque(maxlen=self.daily_boll_period)
        self.bars = deque(maxlen=max(self.ma_period, self.swing_period))
        self.ranges = deque(maxlen=self.atr_period)
        self.last_day = self.last_hour = None
        self.down_daily = False
        self.previous = None

    def update_daily(self, bar: CompletedBar):
        if self.last_day is not None and bar.ts_start != self.last_day:
            self.day_closes.clear()
        self.last_day = bar.ts_end
        self.day_closes.append(bar.close)
        self.down_daily = False
        if len(self.day_closes) < self.daily_boll_period:
            return
        # [统计定义] 完整日线收盘SMA及总体标准差；仅用当时已完成的日线。
        # low穿轨不是收盘正态尾概率，20/2仍是背景规则的固定选择。
        mean = sum(self.day_closes) / self.daily_boll_period
        std = sqrt(sum((x - mean) ** 2 for x in self.day_closes) / self.daily_boll_period)
        self.down_daily = bar.close < mean and bar.low < mean - self.daily_boll_std * std

    def update_hour(self, bar: CompletedBar) -> Features | None:
        if self.last_hour is not None and bar.ts_start != self.last_hour:
            self.bars.clear()
            self.ranges.clear()
            self.previous = None
        self.last_hour = bar.ts_end
        if self.bars:
            previous_close = self.bars[-1].close
            # [计算来源] TR同时覆盖当小时高低差与相对前收盘的跳变；随后算术平均。
            # 14是窗口选择，不使用Wilder指数递推。
            self.ranges.append(
                max(
                    bar.high - bar.low,
                    abs(bar.high - previous_close),
                    abs(bar.low - previous_close),
                )
            )
        self.bars.append(bar)
        if (
            len(self.bars) < max(self.ma_period, self.swing_period)
            or len(self.ranges) < self.atr_period
        ):
            return None
        mean = sum(b.close for b in list(self.bars)[-self.ma_period :]) / self.ma_period
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
            sum(self.ranges) / self.atr_period,
            max(b.high for b in list(self.bars)[-self.swing_period :]),
            self.down_daily and valid_day,
        )
