"""MA20 做多趋势跟随策略。"""

from strategy_python.mechanism_trend_following_long_5min.config import (
    MechanismTrendFollowingLong5MinConfig as MechanismTrendFollowingLong5MinConfig,
)
from strategy_python.mechanism_trend_following_long_5min.strategy import (
    MechanismTrendFollowingLong5MinStrategy as MechanismTrendFollowingLong5MinStrategy,
)

__all__ = ["MechanismTrendFollowingLong5MinConfig", "MechanismTrendFollowingLong5MinStrategy"]
