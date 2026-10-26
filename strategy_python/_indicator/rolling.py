"""Domain-facing warmup semantics over native Nautilus rolling indicators."""

from nautilus_trader.indicators import BollingerBands, MovingAverageType, SimpleMovingAverage


class RollingMean:
    def __init__(self, period):
        if period <= 0:
            raise ValueError("均线周期必须大于 0")
        self.period = period
        self.native = SimpleMovingAverage(period)

    def update(self, value):
        self.native.update_raw(float(value))
        return self.value

    @property
    def value(self):
        return self.native.value if self.native.initialized else None

    def reset(self):
        self.native.reset()


class RollingBollinger:
    def __init__(self, period, multiplier):
        if period <= 0 or multiplier <= 0:
            raise ValueError("BOLL 周期和倍数必须为正数")
        self.period, self.multiplier = period, multiplier
        self.native = BollingerBands(period, multiplier, MovingAverageType.Simple)

    def update(self, value):
        self.native.update_raw(float(value))
        if not self.native.initialized:
            return None
        return self.native.lower, self.native.middle, self.native.upper

    def reset(self):
        self.native.reset()
