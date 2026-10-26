"""MA20 做空趋势跟随策略。"""

from strategy_python.btc_scaled_trend_following_short_optimized_5min.config import (
    BtcScaledTrendFollowingShortOptimized5MinConfig as BtcScaledTrendFollowingShortOptimized5MinConfig,
)
from strategy_python.btc_scaled_trend_following_short_optimized_5min.strategy import (
    BtcScaledTrendFollowingShortOptimized5MinStrategy as BtcScaledTrendFollowingShortOptimized5MinStrategy,
)

__all__ = [
    "BtcScaledTrendFollowingShortOptimized5MinConfig",
    "BtcScaledTrendFollowingShortOptimized5MinStrategy",
]
