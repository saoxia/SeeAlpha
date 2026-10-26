"""MA20 做空趋势跟随策略。"""

from strategy_python.trend_following_short_5min.config import (
    TrendFollowingShort5MinConfig as TrendFollowingShort5MinConfig,
)
from strategy_python.trend_following_short_5min.strategy import (
    TrendFollowingShort5MinStrategy as TrendFollowingShort5MinStrategy,
)

__all__ = ["TrendFollowingShort5MinConfig", "TrendFollowingShort5MinStrategy"]
