"""只服务于 BTCUSDT v4 的滚动指标。"""
from collections import deque

from strategy_python._indicator.rolling import RollingBollinger, RollingMean


class RollingMaDistance:
    """已完成五分钟收盘价减同期 SMA99；满指定窗口后提供分位阈值。"""

    def __init__(self, window_hours: int = 48) -> None:
        self.window_hours = window_hours
        self.sample_count = window_hours * 12
        self.ma = RollingMean(99)
        self.samples: deque[tuple[int, float]] = deque()
        self.current_ma: float | None = None
        self.last_timestamp: int | None = None

    def update(self, timestamp: int, close: float) -> None:
        step = 5 * 60 * 1_000_000_000
        if self.last_timestamp is not None and timestamp - self.last_timestamp != step:
            self.reset()
        self.last_timestamp = timestamp
        self.current_ma = self.ma.update(close)
        if self.current_ma is not None:
            self.samples.append((timestamp, close - self.current_ma))
        cutoff = timestamp - self.window_hours * 60 * 60 * 1_000_000_000
        while self.samples and self.samples[0][0] <= cutoff:
            self.samples.popleft()

    def quantile(self, fraction: float) -> float | None:
        if not 0 <= fraction <= 1:
            raise ValueError("分位比例必须在 0 到 1 之间")
        if len(self.samples) < self.sample_count:
            return None
        values = sorted(value for _, value in self.samples)
        # 严格按剔除后取最低值：剔除数量向下取整，不做线性插值。
        removed = min(int(len(values) * fraction), len(values) - 1)
        return values[removed]

    def range_mean(self, lower_fraction: float, upper_fraction: float, *,
                   negative_only: bool = False) -> float | None:
        """可先筛选负值，再在筛选后的样本中取分位区间均值，包含边界重复值。"""
        if not 0 <= lower_fraction <= upper_fraction <= 1:
            raise ValueError("分位范围必须位于 0 到 1，且下界不大于上界")
        if len(self.samples) < self.sample_count:
            return None
        values = sorted(value for _, value in self.samples if not negative_only or value < 0)
        if not values:
            return None
        low = values[min(int(len(values) * lower_fraction), len(values) - 1)]
        high = values[min(int(len(values) * upper_fraction), len(values) - 1)]
        selected = [value for value in values if low <= value <= high]
        return sum(selected) / len(selected)

    def reset(self) -> None:
        self.ma.reset()
        self.samples.clear()
        self.current_ma = None
        self.last_timestamp = None

__all__ = ["RollingBollinger", "RollingMean"]
