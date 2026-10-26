"""MA20 做多趋势跟随策略。"""

from strategy_python.trend_following_long_5min.config import (
    TrendFollowingLong5MinConfig as TrendFollowingLong5MinConfig,
)
from strategy_python.trend_following_long_5min.strategy import (
    TrendFollowingLong5MinStrategy as TrendFollowingLong5MinStrategy,
)

__all__ = ["TrendFollowingLong5MinConfig", "TrendFollowingLong5MinStrategy"]
