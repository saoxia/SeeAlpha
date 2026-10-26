"""BTCUSDT v4 的分层目标表达。"""

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class EntryTarget:
    """风险层批准后希望挂出的单层限价目标。"""

    direction: int
    role: str
    quantity: Decimal
    price: Decimal
    risk_per_quantity: Decimal = Decimal(0)
    planned_risk: Decimal = Decimal(0)
    risk_context: dict[str, float] | None = None
