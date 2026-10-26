"""MA20 做空趋势跟随策略。"""

from strategy_python.mechanism_trend_following_short_5min.config import (
    MechanismTrendFollowingShort5MinConfig as MechanismTrendFollowingShort5MinConfig,
)
from strategy_python.mechanism_trend_following_short_5min.strategy import (
    MechanismTrendFollowingShort5MinStrategy as MechanismTrendFollowingShort5MinStrategy,
)

__all__ = ["MechanismTrendFollowingShort5MinConfig", "MechanismTrendFollowingShort5MinStrategy"]
