"""无前视的 MA20、标准差和历史最大偏离计算。"""

from collections import deque
from dataclasses import dataclass
from math import fsum, isfinite, sqrt


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
        ma = fsum(values) / self.period
        half_width = self.std * sqrt(fsum((v - ma) ** 2 for v in values) / self.period)
        return ma, ma - half_width, ma + half_width

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


@dataclass(frozen=True)
class TrendLevels:
    ma: float
    std: float
    long_entry: float
    short_entry: float
    long_first_exit: float
    long_final_exit: float
    short_first_exit: float
    short_final_exit: float
    ma_slope: float | None = None


class MaDeviationModel:
    """用每根 K 线当时的 MA 衡量最近窗口内的上下最大偏离。"""

    def __init__(
        self,
        ma_period: int = 20,
        deviation_lookback: int = 52,
        first_exit_std: float = 0.2,
        final_exit_std: float = 0.3,
    ) -> None:
        if ma_period <= 1 or deviation_lookback <= 0:
            raise ValueError("MA 周期必须大于 1，偏离窗口必须大于 0")
        if first_exit_std < 0 or final_exit_std < 0:
            raise ValueError("标准差倍数不能为负")
        self.ma_period = ma_period
        self.deviation_lookback = deviation_lookback
        self.first_exit_std = first_exit_std
        self.final_exit_std = final_exit_std
        self._closes: deque[float] = deque(maxlen=ma_period)
        self._deviations: deque[tuple[float, float]] = deque(maxlen=deviation_lookback)

    @property
    def initialized(self) -> bool:
        return len(self._deviations) == self.deviation_lookback

    def update(self, high: float, low: float, close: float) -> TrendLevels | None:
        # MA(t) - MA(t-1) = (close(t) - close(t-period)) / period.
        slope = (
            (close - self._closes[0]) / self.ma_period
            if len(self._closes) == self.ma_period
            else None
        )
        self._closes.append(close)
        if len(self._closes) < self.ma_period:
            return None

        ma = fsum(self._closes) / self.ma_period
        # [统计定义] σ²=Σ(C-SMA)²/n，使用总体方差；不是收益方差或标准误差。
        # 窗口与退出倍数仍是经验输入，不能由这条公式证明其最优。
        variance = fsum((value - ma) ** 2 for value in self._closes) / self.ma_period
        std = sqrt(variance)
        self._deviations.append((max(0.0, high - ma), max(0.0, ma - low)))
        if not self.initialized:
            return None

        max_up = max(value[0] for value in self._deviations)
        max_down = max(value[1] for value in self._deviations)
        long_entry = ma + max_up
        short_entry = ma - max_down
        return TrendLevels(
            ma=ma,
            ma_slope=slope,
            std=std,
            long_entry=long_entry,
            short_entry=short_entry,
            long_first_exit=ma + self.first_exit_std * std,
            long_final_exit=ma - self.final_exit_std * std,
            short_first_exit=ma - self.first_exit_std * std,
            short_final_exit=ma + self.final_exit_std * std,
        )

    def reset(self) -> None:
        self._closes.clear()
        self._deviations.clear()


class HourlyTrendModel:
    """一小时收盘价与均线斜率同向时允许对应方向入场。"""

    def __init__(
        self,
        period: int = 20,
        slope_lookback: int = 6,
        *,
        use_slope_change: bool = False,
        max_adverse_ma_return: float | None = None,
    ) -> None:
        if period <= 1 or slope_lookback <= 0:
            raise ValueError("MA 周期必须大于 1，斜率窗口必须大于 0")
        self.period = period
        self.slope_lookback = slope_lookback
        if max_adverse_ma_return is not None and (
            not isfinite(max_adverse_ma_return) or max_adverse_ma_return <= 0
        ):
            raise ValueError("逆向小时均线变化率上限必须为有限正数或None")
        self.max_adverse_ma_return = max_adverse_ma_return
        self.use_slope_change = use_slope_change
        self._slopes: deque[float] = deque(maxlen=slope_lookback + 1)
        self._closes: deque[float] = deque(maxlen=period)
        self._mas: deque[float] = deque(maxlen=slope_lookback + 1)
        self.direction = 0
        self.last_close = self.last_ma = self.last_slope = None
        self.last_ma_return = None

    @property
    def initialized(self) -> bool:
        history = self._slopes if self.use_slope_change else self._mas
        return len(history) == self.slope_lookback + 1

    def update(self, close: float) -> int:
        if len(self._closes) == self.period:
            # Algebraically MA(t)-MA(t-1), avoiding cancellation of two MA sums.
            self._slopes.append((close - self._closes[0]) / self.period)
        self._closes.append(close)
        if len(self._closes) < self.period:
            self.direction = 0
            return self.direction
        ma = fsum(self._closes) / self.period
        self._mas.append(ma)
        if not self.initialized:
            self.direction = 0
            return self.direction
        slope = self._slopes[-1] - self._slopes[0] if self.use_slope_change else ma - self._mas[0]
        self.last_close, self.last_ma, self.last_slope = close, ma, slope
        previous_ma = self._mas[-2]
        self.last_ma_return = self._slopes[-1] / previous_ma if previous_ma > 0 else None
        self.direction = 1 if close > ma and slope > 0 else -1 if close < ma and slope < 0 else 0
        if self.direction and self.max_adverse_ma_return is not None:
            previous_ma = self._mas[-2]
            if (
                previous_ma <= 0
                or self.direction * self._slopes[-1] <= -self.max_adverse_ma_return * previous_ma
            ):
                self.direction = 0
        return self.direction

    def allows_short(self, *, require_price: bool = True, require_slope: bool = True) -> bool:
        """单项消融保留其余小时条件；关闭斜率过滤不改用另一种斜率规则。"""
        if not self.initialized or self.last_ma is None or self.last_ma_return is None:
            return False
        return (
            (not require_price or self.last_close < self.last_ma)
            and (not require_slope or self.last_slope < 0)
            and (
                self.max_adverse_ma_return is None
                or self.last_ma_return < self.max_adverse_ma_return
            )
        )

    def reset(self) -> None:
        self._closes.clear()
        self._mas.clear()
        self._slopes.clear()
        self.direction = 0
        self.last_close = self.last_ma = self.last_slope = None
        self.last_ma_return = None


class MaCrossModel:
    """Only emit a crossing on a completed bar; no signal on first initialization."""

    def __init__(self, fast: int = 20, slow: int = 99) -> None:
        if not 1 < fast < slow:
            raise ValueError("交叉均线须满足1<快线周期<慢线周期")
        self.fast, self.slow = fast, slow
        self.closes: deque[float] = deque(maxlen=slow)
        self.previous_difference: float | None = None

    def update(self, close: float) -> int:
        self.closes.append(close)
        if len(self.closes) < self.slow:
            return 0
        values = list(self.closes)
        # [计算来源] 两个当时可见收盘均值之差；只在相对上一根跨零时触发。
        # 52/99的窗口选择与均值公式分开，尚无市场周期估计证明精确窗口。
        difference = fsum(values[-self.fast :]) / self.fast - fsum(values) / self.slow
        previous = self.previous_difference
        self.previous_difference = difference
        if previous is None:
            return 0
        if previous <= 0 < difference:
            return 1
        if previous >= 0 > difference:
            return -1
        return 0

    def reset(self) -> None:
        self.closes.clear()
        self.previous_difference = None
