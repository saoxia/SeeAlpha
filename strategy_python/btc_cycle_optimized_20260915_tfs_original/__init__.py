"""MA20 做空趋势跟随策略。"""

from strategy_python.btc_cycle_optimized_20260915_tfs_original.config import (
    MechanismTrendFollowingShort5MinConfig as MechanismTrendFollowingShort5MinConfig,
)
from strategy_python.btc_cycle_optimized_20260915_tfs_original.strategy import (
    MechanismTrendFollowingShort5MinStrategy as MechanismTrendFollowingShort5MinStrategy,
)

__all__ = ["MechanismTrendFollowingShort5MinConfig", "MechanismTrendFollowingShort5MinStrategy"]

from .preset import create_config as create_config
from .preset import create_strategy as create_strategy
