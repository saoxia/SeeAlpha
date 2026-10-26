"""MA20 做空趋势跟随策略。"""

from strategy_python._optimization.btc_cycle_20260915.next_packages.tfs_optimized.config import (
    MechanismTrendFollowingShort5MinConfig,
)
from strategy_python._optimization.btc_cycle_20260915.next_packages.tfs_optimized.strategy import (
    MechanismTrendFollowingShort5MinStrategy,
)

__all__ = ["MechanismTrendFollowingShort5MinConfig", "MechanismTrendFollowingShort5MinStrategy"]
