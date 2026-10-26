"""趋势策略的高低收 K 线聚合适配。"""

from dataclasses import dataclass

from strategy_python._indicator.bars import CompletedBarAggregator as OhlcAggregator

NANOSECONDS_PER_MINUTE = 60_000_000_000


@dataclass(frozen=True)
class FiveMinuteBar:
    high: float
    low: float
    close: float


class CompletedBarAggregator:
    def __init__(
        self,
        minutes: int,
        source_minutes: int = 1,
        *,
        source_seconds: int | None = None,
    ) -> None:
        self._aggregator = OhlcAggregator(
            minutes,
            source_seconds=source_minutes * 60 if source_seconds is None else source_seconds,
        )

    def update(self, ts_event: int, high: float, low: float, close: float) -> FiveMinuteBar | None:
        result = self._aggregator.update(ts_event, close, high, low, close)
        return FiveMinuteBar(result.high, result.low, result.close) if result else None

    def reset(self) -> None:
        self._aggregator.reset()


class FiveMinuteAggregator(CompletedBarAggregator):
    def __init__(self, *, source_seconds: int = 60) -> None:
        super().__init__(5, source_seconds=source_seconds)
