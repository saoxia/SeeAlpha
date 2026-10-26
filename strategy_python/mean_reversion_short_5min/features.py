"""ETHUSDT v4 的多周期聚合与指标特征状态。"""

from collections import deque
from math import isfinite

from nautilus_trader.model import Bar

from strategy_python._indicator.bars import source_seconds_for
from strategy_python.mean_reversion_short_5min.bars import CompletedBar, CompletedBarAggregator
from strategy_python.mean_reversion_short_5min.config import MeanReversionShort5MinConfig
from strategy_python.mean_reversion_short_5min.indicators import (
    RollingBollinger,
    RollingMaDistance,
)


class BollingerTouchFilter:
    """按独立周期的同期 BOLL 记录最近触碰侧。"""

    def __init__(self, minutes: int, period: int, multiplier: float) -> None:
        self.minutes = minutes
        self._bollinger = RollingBollinger(period, multiplier)
        self.reset()

    def update(self, bar: CompletedBar) -> None:
        if self.last_bar_ns is not None and bar.ts_end <= self.last_bar_ns:
            return
        if self.last_bar_ns is not None and bar.ts_start != self.last_bar_ns:
            self.reset()
        self.last_bar_ns = bar.ts_end
        if not all(isfinite(v) for v in (bar.high, bar.low, bar.close)):
            self.reset()
            return
        self.bollinger = self._bollinger.update(bar.close)
        if self.bollinger is None:
            return
        lower, _, upper = self.bollinger
        touches = (int(bar.high >= upper), int(bar.low <= lower))
        self.touch_counts.append(touches)
        if any(touches):
            self.recent_touches.append(touches)
        if bar.high >= upper:
            self.upper_touch_ns = bar.ts_end
        if bar.low <= lower:
            self.lower_touch_ns = bar.ts_end

    def allowed(self, now_ns: int) -> bool:
        if (self.bollinger is None or self.last_bar_ns is None
                or now_ns < self.last_bar_ns
                or now_ns - self.last_bar_ns >= self.minutes * 60_000_000_000):
            return False
        upper, lower = self.upper_touch_ns, self.lower_touch_ns
        return lower is not None and (upper is None or lower > upper)

    def reset(self) -> None:
        self._bollinger.reset()
        self.bollinger = None
        self.upper_touch_ns = None
        self.lower_touch_ns = None
        self.last_bar_ns = None
        self.touch_counts: deque[tuple[int, int]] = deque(maxlen=36)
        self.recent_touches: deque[tuple[int, int]] = deque(maxlen=3)

    def frequency_allowed(self) -> bool:
        """最近36根有同期BOLL的完整K线，上轨和下轨各至少触碰2次。"""
        return (len(self.touch_counts) == 36
                and sum(upper for upper, _ in self.touch_counts) >= 2
                and sum(lower for _, lower in self.touch_counts) >= 2)

    def recent_lower_touch_allowed(self) -> bool:
        """最近3根发生轨道触碰的K线中，至少一根触碰下轨。"""
        return len(self.recent_touches) == 3 and any(lower for _, lower in self.recent_touches)


class EthFeatureModel:
    """消费市场数据并维护指标；不知道账户、仓位和订单。"""

    def __init__(self, config: MeanReversionShort5MinConfig) -> None:
        self.entry_ma_distance_quantile = config.entry_ma_distance_quantile
        self.ma_distance = RollingMaDistance()
        self.stop_ma_distance = RollingMaDistance(window_hours=14 * 24)
        self.stop_p995: float | None = None
        self.add_distance_mean: float | None = None
        self.take_profit_distance_mean: float | None = None
        source_seconds = source_seconds_for(config.bar_type)
        self._aggregators = {minutes: CompletedBarAggregator(minutes, source_seconds=source_seconds)
                             for minutes in (5,)}
        self._bollinger = RollingBollinger(
            config.bollinger_period,
            config.bollinger_std_multiplier,
        )
        self.bollinger: tuple[float, float, float] | None = None
        self.bollinger_widths: deque[float] = deque(maxlen=240)
        self.min_bollinger_width: float | None = None
        self._last_five_minute_ns: int | None = None
        self.entry_filters = {
            minutes: BollingerTouchFilter(minutes, config.bollinger_period, config.bollinger_std_multiplier)
            for minutes in (5,)
        }

    def aggregate(self, bar: Bar) -> dict[int, CompletedBar | None]:
        values = (
            bar.ts_event,
            bar.open.as_double(),
            bar.high.as_double(),
            bar.low.as_double(),
            bar.close.as_double(),
        )
        return {
            minutes: aggregator.update(*values) for minutes, aggregator in self._aggregators.items()
        }

    def on_five_minute_bar(self, bar: CompletedBar) -> tuple[float, float, float] | None:
        if self._last_five_minute_ns is not None:
            if bar.ts_end <= self._last_five_minute_ns:
                return self.bollinger
            if bar.ts_end - self._last_five_minute_ns != 300_000_000_000:
                self._bollinger.reset()
                self.bollinger_widths.clear()
                self.min_bollinger_width = None
        self._last_five_minute_ns = bar.ts_end
        self.entry_filters[5].update(bar)
        if not all(isfinite(value) for value in (bar.high, bar.low, bar.close)):
            self._bollinger.reset()
            self.bollinger = None
            self.bollinger_widths.clear()
            self.min_bollinger_width = None
            return None
        self.ma_distance.update(bar.ts_end, bar.close)
        self.add_distance_mean = self.ma_distance.range_mean(.80, .95)
        self.take_profit_distance_mean = self.ma_distance.range_mean(.20, .70, negative_only=True)
        self.stop_ma_distance.update(bar.ts_end, bar.close)
        self.stop_p995 = self.stop_ma_distance.quantile(.995)
        self.bollinger = self._bollinger.update(bar.close)
        if self.bollinger is not None:
            self.bollinger_widths.append(self.bollinger[2] - self.bollinger[0])
            self.min_bollinger_width = (
                min(self.bollinger_widths) if len(self.bollinger_widths) == 240 else None
            )
        return self.bollinger

    def entry_touch_allowed(self, now_ns: int) -> bool:
        return all(filter_.allowed(now_ns) and filter_.frequency_allowed()
                   and filter_.recent_lower_touch_allowed()
                   for filter_ in self.entry_filters.values())

    def entry_distance_allowed(self, price: float) -> bool:
        fraction = self.entry_ma_distance_quantile
        threshold = self.ma_distance.quantile(fraction)
        current_ma = self.ma_distance.current_ma
        return (
            threshold is not None and current_ma is not None
            and isfinite(price) and isfinite(current_ma) and isfinite(threshold)
            and price - current_ma >= threshold
        )

    def reset(self) -> None:
        for filter_ in self.entry_filters.values():
            filter_.reset()
        for aggregator in self._aggregators.values():
            aggregator.reset()
        self.ma_distance.reset()
        self.stop_ma_distance.reset()
        self.stop_p995 = None
        self._bollinger.reset()
        self.bollinger = None
        self.bollinger_widths.clear()
        self.min_bollinger_width = None
        self._last_five_minute_ns = None
        self.add_distance_mean = None
        self.take_profit_distance_mean = None
