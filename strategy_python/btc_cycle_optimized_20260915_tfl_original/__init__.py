"""MA20 做多趋势跟随策略。"""

from strategy_python.btc_cycle_optimized_20260915_tfl_original.config import (
    MechanismTrendFollowingLong5MinConfig as MechanismTrendFollowingLong5MinConfig,
)
from strategy_python.btc_cycle_optimized_20260915_tfl_original.strategy import (
    MechanismTrendFollowingLong5MinStrategy as MechanismTrendFollowingLong5MinStrategy,
)

__all__ = ["MechanismTrendFollowingLong5MinConfig", "MechanismTrendFollowingLong5MinStrategy"]

from .preset import create_config as create_config
from .preset import create_strategy as create_strategy
