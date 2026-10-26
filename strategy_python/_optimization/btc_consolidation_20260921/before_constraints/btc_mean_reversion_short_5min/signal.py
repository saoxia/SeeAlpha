"""BTCUSDT v4 的首仓 Alpha 信号。"""

from dataclasses import dataclass
from math import isfinite

from strategy_python.btc_mean_reversion_short_5min.config import BtcMeanReversionShort5MinConfig
from strategy_python.btc_mean_reversion_short_5min.state import DirectionState


@dataclass(frozen=True)
class EthEntrySignal:
    direction: int
    price: float
    timestamp_ns: int
    reason: str


class EthEntrySignalModel:
    """只回答首仓方向与价格，不访问资金或订单 API。"""

    def __init__(self, config: BtcMeanReversionShort5MinConfig) -> None:
        """保留构造接口；首仓信号检查持仓状态及止损后等待锁。"""

    def evaluate(
        self,
        state: DirectionState,
        now_ns: int,
        bollinger: tuple[float, float, float] | None,
    ) -> EthEntrySignal | None:
        if state.invested or state.reentry_waiting or bollinger is None:
            return None
        _, _, upper = bollinger
        if not isfinite(upper) or upper <= 0:
            return None
        return EthEntrySignal(
            direction=state.direction,
            price=upper,
            timestamp_ns=now_ns,
            reason="boll_initial_entry",
        )
