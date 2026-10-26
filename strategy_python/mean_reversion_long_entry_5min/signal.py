"""ETHUSDT v4 的首仓 Alpha 信号。"""

from dataclasses import dataclass

from strategy_python.mean_reversion_long_entry_5min.config import (
    EntryMeanReversionLong5MinConfig,
)
from strategy_python.mean_reversion_long_entry_5min.state import (
    DirectionState,
)


@dataclass(frozen=True)
class EthEntrySignal:
    direction: int
    price: float
    timestamp_ns: int
    reason: str


class EthEntrySignalModel:
    """只回答首仓方向与价格，不访问资金或订单 API。"""

    def __init__(self, config: EntryMeanReversionLong5MinConfig) -> None:
        self._trade_start_ns = config.trade_start_ns
        self._ma_reentry_ratio = config.ma_reentry_ratio

    def evaluate(
        self,
        state: DirectionState,
        now_ns: int,
        bollinger: tuple[float, float, float] | None,
        adverse_ma99_ratio: float | None,
    ) -> EthEntrySignal | None:
        if bollinger is None or (self._trade_start_ns and now_ns < self._trade_start_ns):
            return None
        if now_ns < state.cooldown_until_ns or state.ma52_recovery_waiting:
            return None
        if adverse_ma99_ratio is None or adverse_ma99_ratio > self._ma_reentry_ratio + 1e-12:
            return None
        lower, _, _ = bollinger
        return EthEntrySignal(
            direction=state.direction,
            price=lower,
            timestamp_ns=now_ns,
            reason="boll_initial_entry",
        )
