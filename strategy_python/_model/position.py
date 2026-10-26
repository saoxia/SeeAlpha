"""与交易场所和订单 API 无关的目标仓位模型。"""

from dataclasses import dataclass
from decimal import Decimal
from enum import IntEnum


class PositionSide(IntEnum):
    """当前或目标持仓方向。"""

    SHORT = -1
    FLAT = 0
    LONG = 1


@dataclass(frozen=True)
class PositionTarget:
    """策略希望执行层最终达到的仓位，而不是具体订单。"""

    side: PositionSide
    quantity: Decimal
    reason: str
