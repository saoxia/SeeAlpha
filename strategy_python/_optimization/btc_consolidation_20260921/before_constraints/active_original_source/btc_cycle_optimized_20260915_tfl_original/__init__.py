"""MA20 做多趋势跟随策略。"""

from strategy_python.btc_cycle_optimized_20260915_tfl_original.config import (
    MechanismTrendFollowingLong5MinConfig,
)
from strategy_python.btc_cycle_optimized_20260915_tfl_original.strategy import (
    MechanismTrendFollowingLong5MinStrategy,
)

__all__ = ["MechanismTrendFollowingLong5MinConfig", "MechanismTrendFollowingLong5MinStrategy"]

from .preset import create_config, create_strategy
