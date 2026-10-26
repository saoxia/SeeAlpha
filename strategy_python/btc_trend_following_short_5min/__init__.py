"""MA20 做空趋势跟随策略。"""

from strategy_python.btc_trend_following_short_5min.config import (
    BtcTrendFollowingShort5MinConfig as BtcTrendFollowingShort5MinConfig,
)
from strategy_python.btc_trend_following_short_5min.strategy import (
    BtcTrendFollowingShort5MinStrategy as BtcTrendFollowingShort5MinStrategy,
)

__all__ = ["BtcTrendFollowingShort5MinConfig", "BtcTrendFollowingShort5MinStrategy"]
