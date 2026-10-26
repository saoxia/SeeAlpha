"""MA20 做多趋势跟随策略。"""

from strategy_python.btc_scaled_trend_following_long_optimized_5min.config import (
    BtcScaledTrendFollowingLongOptimized5MinConfig as BtcScaledTrendFollowingLongOptimized5MinConfig,
)
from strategy_python.btc_scaled_trend_following_long_optimized_5min.strategy import (
    BtcScaledTrendFollowingLongOptimized5MinStrategy as BtcScaledTrendFollowingLongOptimized5MinStrategy,
)

__all__ = [
    "BtcScaledTrendFollowingLongOptimized5MinConfig",
    "BtcScaledTrendFollowingLongOptimized5MinStrategy",
]
