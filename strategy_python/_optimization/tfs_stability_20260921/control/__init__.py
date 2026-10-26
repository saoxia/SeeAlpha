"""MA20 做空趋势跟随策略。"""

from strategy_python._optimization.tfs_stability_20260921.control.config import (
    TrendFollowingShortOptimized5MinConfig,
)
from strategy_python._optimization.tfs_stability_20260921.control.strategy import (
    TrendFollowingShortOptimized5MinStrategy,
)

__all__ = ["TrendFollowingShortOptimized5MinConfig", "TrendFollowingShortOptimized5MinStrategy"]
