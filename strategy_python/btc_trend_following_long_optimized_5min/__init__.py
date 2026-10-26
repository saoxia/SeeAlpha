"""MA20 做多趋势跟随策略。"""

from strategy_python.btc_trend_following_long_optimized_5min.config import (
    BtcTrendFollowingLongOptimized5MinConfig as BtcTrendFollowingLongOptimized5MinConfig,
)
from strategy_python.btc_trend_following_long_optimized_5min.strategy import (
    BtcTrendFollowingLongOptimized5MinStrategy as BtcTrendFollowingLongOptimized5MinStrategy,
)

__all__ = ["BtcTrendFollowingLongOptimized5MinConfig", "BtcTrendFollowingLongOptimized5MinStrategy"]
