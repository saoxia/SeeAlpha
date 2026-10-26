"""下跌趋势反弹做空。"""

from strategy_python.btc_pullback_short_1h.config import (
    BtcPullbackShort1hConfig as BtcPullbackShort1hConfig,
)
from strategy_python.btc_pullback_short_1h.strategy import (
    BtcPullbackShort1hStrategy as BtcPullbackShort1hStrategy,
)

__all__ = ["BtcPullbackShort1hConfig", "BtcPullbackShort1hStrategy"]
