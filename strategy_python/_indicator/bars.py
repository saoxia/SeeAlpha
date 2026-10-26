"""各策略共用的 UTC 已闭合 K 线聚合。"""

from dataclasses import dataclass

from nautilus_trader.indicators import DonchianChannel
from nautilus_trader.model import BarAggregation, BarType

NANOSECONDS_PER_MINUTE = 60_000_000_000
NANOSECONDS_PER_HOUR = 60 * NANOSECONDS_PER_MINUTE
NANOSECONDS_PER_DAY = 24 * NANOSECONDS_PER_HOUR


def source_seconds_for(bar_type: BarType, target_seconds: int = 300) -> int:
    """输入必须是可以完整组成策略最小信号周期的外部时间线。"""
    units = {BarAggregation.SECOND: 1, BarAggregation.MINUTE: 60, BarAggregation.HOUR: 3600}
    seconds = units.get(bar_type.spec.aggregation, 0) * bar_type.spec.step
    if not bar_type.is_externally_aggregated() or seconds <= 0 or target_seconds % seconds:
        raise ValueError("输入周期必须能整除 5 分钟（例如 1s、1m、5m）")
    return seconds


@dataclass(frozen=True)
class CompletedBar:
    ts_start: int
    ts_end: int
    open: float
    high: float
    low: float
    close: float


class CompletedBarAggregator:
    """Native OHLC extrema with the project's strict contiguous-period gate.

    rc7 exports DonchianChannel but no standalone TimeBarAggregator to Python.
    Native extrema preserve synchronous closed-bar callbacks; only completeness,
    UTC boundaries and first/last observation identity belong to this adapter.
    """

    def __init__(self, minutes: int, *, source_seconds: int = 60) -> None:
        if minutes <= 0 or source_seconds <= 0 or (minutes * 60) % source_seconds:
            raise ValueError("聚合周期必须是输入周期的正整数倍")
        self.minutes = minutes
        self._period_ns = minutes * NANOSECONDS_PER_MINUTE
        self._source_ns = source_seconds * 1_000_000_000
        self.expected_count = minutes * 60 // source_seconds
        self.native = DonchianChannel(self.expected_count)
        self.reset()

    def update(
        self,
        ts_event: int,
        open_price: float,
        high: float,
        low: float,
        close: float,
    ) -> CompletedBar | None:
        end = ts_event + 1_000_000
        if end % self._source_ns or (self._last_end is not None and end <= self._last_end):
            # 重复、倒序、错位的数据不能补足缺失的输入线。
            self._complete = False
            return None
        bucket_end = ((end + self._period_ns - 1) // self._period_ns) * self._period_ns
        if bucket_end != self._bucket_end:
            self._bucket_end = bucket_end
            self._complete = end - self._source_ns == bucket_end - self._period_ns
            self.native.reset()
            self._open = open_price
            self._count = 1
        else:
            self._complete = self._complete and end - self._source_ns == self._last_end
            self._count += 1
        self.native.update_raw(high, low)
        self._last_end, self._close = end, close
        if end != bucket_end or not self._complete or self._count != self.expected_count:
            return None
        return CompletedBar(
            bucket_end - self._period_ns,
            bucket_end,
            self._open,
            self.native.upper,
            self.native.lower,
            self._close,
        )

    def reset(self) -> None:
        self.native.reset()
        self._bucket_end = 0
        self._last_end: int | None = None
        self._complete = False
        self._count = 0
        self._open = self._close = 0.0
