"""无前视的 MA20、标准差和历史最大偏离计算。"""
from collections import deque
from math import isfinite

from nautilus_trader.indicators import (
    BollingerBands,
    MovingAverageType,
)

from strategy_python._indicator.trend import (
    HourlyTrendModel,
    MaCrossModel,
    MaDeviationModel,
    TrendLevels,
)


class DailyBollEntryFilter:
    """按动态日线带宽和价格位置，决定是否要求连续两日最低价低于各自下轨。

    布林带采用收盘价 SMA 和总体标准差。当日用最新收盘价替代日收盘价，
    不锁存盘中曾满足的信号；缺失/不连续日线会使历史窗口重新预热。
    max_width_ratio=None 时始终检查两日低点，否则仅在带宽比严格小于阈值，
    且最新价处于下轨至中轨（含边界）时检查。数据未就绪仍禁止建仓。
    upper_max_width_ratio 可单独限制上轨及以上的价格：带宽比须严格小于阈值。
    """

    DAY_NS = 86_400_000_000_000

    def __init__(
        self,
        period: int = 20,
        std: float = 2.0,
        *,
        source_seconds: int = 60,
        max_width_ratio: float | None = None,
        upper_max_width_ratio: float | None = None,
    ):
        if period < 2 or not isfinite(std) or std <= 0:
            raise ValueError("日线 BOLL 周期须至少为 2，标准差倍数须为有限正数")
        if source_seconds <= 0 or 86400 % source_seconds:
            raise ValueError("输入周期须整除一天")
        if max_width_ratio is not None and (not isfinite(max_width_ratio) or max_width_ratio <= 0):
            raise ValueError("日线 BOLL 带宽比上限须为有限正数或 None")
        if upper_max_width_ratio is not None and (
            not isfinite(upper_max_width_ratio) or upper_max_width_ratio <= 0
        ):
            raise ValueError("日线 BOLL 上轨带宽比上限须为有限正数或 None")
        self.period, self.std = period, std
        self.max_width_ratio = max_width_ratio
        self.upper_max_width_ratio = upper_max_width_ratio
        self.source_ns = source_seconds * 1_000_000_000
        self.closes: deque[float] = deque(maxlen=period)
        self.reset()

    def reset(self) -> None:
        self.closes.clear()
        self.day_end: int | None = None
        self.last_end: int | None = None
        self.complete = False
        self.low = self.close = 0.0
        self.previous_below = False

    def _bands(self, values: list[float]) -> tuple[float, float, float]:
        native = BollingerBands(self.period, self.std, MovingAverageType.Simple)
        for value in values:
            native.update_raw(value)
        return native.middle, native.lower, native.upper

    def _lower(self, values: list[float]) -> float:
        return self._bands(values)[1]

    @property
    def allowed(self) -> bool:
        if not self.complete or len(self.closes) < self.period:
            return False
        values = list(self.closes)[-(self.period - 1) :] + [self.close]
        ma, lower, upper = self._bands(values)
        if ma <= 0:
            return False
        width_ratio = (upper - lower) / ma
        if (
            self.upper_max_width_ratio is not None
            and self.close >= upper
            and width_ratio >= self.upper_max_width_ratio
        ):
            return False
        if self.max_width_ratio is not None and not (
            width_ratio < self.max_width_ratio and lower <= self.close <= ma
        ):
            return True
        return self.previous_below and self.low < lower

    def update(self, ts_event: int, low: float, close: float) -> None:
        end = ts_event + 1_000_000
        if end % self.source_ns or (self.last_end is not None and end <= self.last_end):
            self.complete = False
            self.previous_below = False
            return
        day_end = ((end + self.DAY_NS - 1) // self.DAY_NS) * self.DAY_NS
        if day_end != self.day_end:
            if (
                self.day_end is not None
                and day_end == self.day_end + self.DAY_NS
                and self.last_end == self.day_end
                and self.complete
            ):
                self.closes.append(self.close)
                self.previous_below = len(self.closes) == self.period and self.low < self._lower(
                    list(self.closes)
                )
            else:
                self.closes.clear()
                self.previous_below = False
            self.day_end = day_end
            self.complete = end - self.source_ns == day_end - self.DAY_NS
            self.low = low
        else:
            self.complete = self.complete and end - self.source_ns == self.last_end
            self.low = min(self.low, low)
        self.close, self.last_end = close, end









__all__ = ['DailyBollEntryFilter', 'HourlyTrendModel', 'MaCrossModel', 'MaDeviationModel', 'TrendLevels']
