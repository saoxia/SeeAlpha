"""只服务于 ETHUSDT v4 的滚动指标。"""

from collections import deque
from math import sqrt


class RollingMean:
    """只在样本填满后给出简单移动平均。"""

    def __init__(self, period: int) -> None:
        if period <= 0:
            raise ValueError("均线周期必须大于 0")
        self.values: deque[float] = deque(maxlen=period)
        self.period = period

    def update(self, value: float) -> float | None:
        self.values.append(float(value))
        return self.value

    @property
    def value(self) -> float | None:
        if len(self.values) < self.period:
            return None
        return sum(self.values) / self.period

    def reset(self) -> None:
        self.values.clear()


class RollingBollinger:
    """与交接策略一致的 SMA 中轨和总体标准差布林带。"""

    def __init__(self, period: int, multiplier: float) -> None:
        if period <= 0 or multiplier <= 0:
            raise ValueError("BOLL 周期和倍数必须为正数")
        self.values: deque[float] = deque(maxlen=period)
        self.period = period
        self.multiplier = multiplier

    def update(self, value: float) -> tuple[float, float, float] | None:
        self.values.append(float(value))
        if len(self.values) < self.period:
            return None
        # [计算来源] 输入为当时已完成的5分钟收盘：SMA=ΣC/n，σ=sqrt(Σ(C-SMA)²/n)。
        # 总体标准差用于价格尺度归一化；n和k的选值依据见PARAMETER_SOURCES.md。
        # 这里没有正态分布假设；k不能直接转换为穿轨概率或胜率。
        middle = sum(self.values) / self.period
        deviation = sqrt(sum((item - middle) ** 2 for item in self.values) / self.period)
        return (
            middle - self.multiplier * deviation,
            middle,
            middle + self.multiplier * deviation,
        )

    def reset(self) -> None:
        self.values.clear()
