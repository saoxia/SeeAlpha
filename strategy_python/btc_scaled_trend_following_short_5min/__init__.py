"""MA20 做空趋势跟随策略。"""

from strategy_python.btc_scaled_trend_following_short_5min.config import (
    BtcScaledTrendFollowingShort5MinConfig as BtcScaledTrendFollowingShort5MinConfig,
)
from strategy_python.btc_scaled_trend_following_short_5min.strategy import (
    BtcScaledTrendFollowingShort5MinStrategy as BtcScaledTrendFollowingShort5MinStrategy,
)

__all__ = ["BtcScaledTrendFollowingShort5MinConfig", "BtcScaledTrendFollowingShort5MinStrategy"]
