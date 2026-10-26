"""单份持仓和订单的运行状态。"""

from dataclasses import dataclass
from decimal import Decimal


@dataclass
class TradeState:
    quantity: Decimal = Decimal(0)
    entry_value: Decimal = Decimal(0)
    stop: Decimal = Decimal(0)
    target: Decimal = Decimal(0)
    opened_ns: int = 0
    position_id: object = None
    entry_id: object = None
    stop_id: object = None
    target_id: object = None
    exit_id: object = None
    exiting: bool = False
