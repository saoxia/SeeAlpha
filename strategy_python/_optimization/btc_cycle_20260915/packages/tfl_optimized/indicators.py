"""无前视的 MA20、标准差和历史最大偏离计算。"""

from collections import deque
from dataclasses import dataclass
from math import fsum, isfinite, sqrt


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
        self.direction = 1 if close > ma and slope > 0 else -1 if close < ma and slope < 0 else 0
        if self.direction and self.max_adverse_ma_return is not None:
            previous_ma = self._mas[-2]
            if (
                previous_ma <= 0
                or self.direction * self._slopes[-1] <= -self.max_adverse_ma_return * previous_ma
            ):
                self.direction = 0
        return self.direction

    def reset(self) -> None:
        self._closes.clear()
        self._mas.clear()
        self._slopes.clear()
        self.direction = 0


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
