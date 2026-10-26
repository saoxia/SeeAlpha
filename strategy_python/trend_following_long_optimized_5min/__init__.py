"""MA20 做多趋势跟随策略。"""

from strategy_python.trend_following_long_optimized_5min.config import (
    TrendFollowingLongOptimized5MinConfig as TrendFollowingLongOptimized5MinConfig,
)
from strategy_python.trend_following_long_optimized_5min.strategy import (
    TrendFollowingLongOptimized5MinStrategy as TrendFollowingLongOptimized5MinStrategy,
)

__all__ = ["TrendFollowingLongOptimized5MinConfig", "TrendFollowingLongOptimized5MinStrategy"]
