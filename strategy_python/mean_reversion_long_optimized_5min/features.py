"""ETHUSDT v4 的多周期聚合与指标特征状态。"""

from collections import deque

from nautilus_trader.model import Bar

from strategy_python._indicator.bars import source_seconds_for
from strategy_python.mean_reversion_long_optimized_5min.bars import (
    CompletedBar,
    CompletedBarAggregator,
)
from strategy_python.mean_reversion_long_optimized_5min.config import (
    MeanReversionLongOptimized5MinConfig,
)
from strategy_python.mean_reversion_long_optimized_5min.indicators import (
    RollingBollinger,
    RollingMean,
)
from strategy_python.mean_reversion_long_optimized_5min.state import DirectionState


class EthFeatureModel:
    """消费市场数据并维护指标；不知道账户、仓位和订单。"""

    def __init__(self, config: MeanReversionLongOptimized5MinConfig) -> None:
        self._ma_filter_window = config.ma_filter_window
        source_seconds = source_seconds_for(config.bar_type)
        self._aggregators = {
            5: CompletedBarAggregator(5, source_seconds=source_seconds),
            15: CompletedBarAggregator(15, source_seconds=source_seconds),
            60: CompletedBarAggregator(60, source_seconds=source_seconds),
            360: CompletedBarAggregator(360, source_seconds=source_seconds),
            1440: CompletedBarAggregator(1440, source_seconds=source_seconds),
        }
        self._bollinger = RollingBollinger(
            config.bollinger_period,
            config.bollinger_std_multiplier,
        )
        self.bollinger: tuple[float, float, float] | None = None
        self._ma99 = RollingMean(config.ma_filter_period)
        self._ma99_states: deque[bool] = deque(maxlen=config.ma_filter_window)
        self._ma52_six_hour = RollingMean(config.recovery_ma_period)
        self._ma52_daily = RollingMean(config.recovery_ma_period)
        self.six_hour_history: deque[CompletedBar] = deque(
            maxlen=config.recovery_snapshot_bars,
        )

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
        self.bollinger = self._bollinger.update(bar.close)
        return self.bollinger

    def on_fifteen_minute_bar(self, bar: CompletedBar) -> None:
        ma99 = self._ma99.update(bar.close)
        if ma99 is not None:
            self._ma99_states.append(bar.low < ma99)

    def on_six_hour_bar(self, bar: CompletedBar) -> float | None:
        ma52 = self._ma52_six_hour.update(bar.close)
        if ma52 is not None:
            self.six_hour_history.append(bar)
        return ma52

    def on_daily_bar(self, bar: CompletedBar) -> float | None:
        return self._ma52_daily.update(bar.close)

    def adverse_ma99_ratio(self, state: DirectionState) -> float | None:
        if len(self._ma99_states) < self._ma_filter_window:
            return None
        # [计算来源] 每个状态为当根15分钟low<当根SMA，求经验出现比例。
        # 重叠均线使这些状态相关；不能把窗口长度当作独立样本数。
        return sum(self._ma99_states) / len(self._ma99_states)

    def reset(self) -> None:
        for aggregator in self._aggregators.values():
            aggregator.reset()
        self._bollinger.reset()
        self.bollinger = None
        self._ma99.reset()
        self._ma99_states.clear()
        self._ma52_six_hour.reset()
        self._ma52_daily.reset()
        self.six_hour_history.clear()
