"""MA20 做空趋势跟随策略。"""

from strategy_python.btc_trend_following_short_optimized_5min.config import (
    BtcTrendFollowingShortOptimized5MinConfig as BtcTrendFollowingShortOptimized5MinConfig,
)
from strategy_python.btc_trend_following_short_optimized_5min.strategy import (
    BtcTrendFollowingShortOptimized5MinStrategy as BtcTrendFollowingShortOptimized5MinStrategy,
)

__all__ = [
    "BtcTrendFollowingShortOptimized5MinConfig",
    "BtcTrendFollowingShortOptimized5MinStrategy",
]
