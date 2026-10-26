"""MA20 做空趋势跟随策略。"""

from strategy_python.trend_following_short_optimized_5min.config import (
    TrendFollowingShortOptimized5MinConfig as TrendFollowingShortOptimized5MinConfig,
)
from strategy_python.trend_following_short_optimized_5min.strategy import (
    TrendFollowingShortOptimized5MinStrategy as TrendFollowingShortOptimized5MinStrategy,
)

__all__ = ["TrendFollowingShortOptimized5MinConfig", "TrendFollowingShortOptimized5MinStrategy"]
