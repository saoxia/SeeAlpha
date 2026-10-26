"""MA20 做多趋势跟随策略。"""

from strategy_python.btc_scaled_trend_following_long_5min.config import (
    BtcScaledTrendFollowingLong5MinConfig as BtcScaledTrendFollowingLong5MinConfig,
)
from strategy_python.btc_scaled_trend_following_long_5min.strategy import (
    BtcScaledTrendFollowingLong5MinStrategy as BtcScaledTrendFollowingLong5MinStrategy,
)

__all__ = ["BtcScaledTrendFollowingLong5MinConfig", "BtcScaledTrendFollowingLong5MinStrategy"]
