"""MA20 做多趋势跟随策略。"""

from strategy_python.btc_trend_following_long_5min.config import (
    BtcTrendFollowingLong5MinConfig as BtcTrendFollowingLong5MinConfig,
)
from strategy_python.btc_trend_following_long_5min.strategy import (
    BtcTrendFollowingLong5MinStrategy as BtcTrendFollowingLong5MinStrategy,
)

__all__ = ["BtcTrendFollowingLong5MinConfig", "BtcTrendFollowingLong5MinStrategy"]
