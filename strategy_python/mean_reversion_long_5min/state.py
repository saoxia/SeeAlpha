"""ETHUSDT v4 策略运行期间的可变状态。"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from strategy_python.mean_reversion_long_5min.bars import CompletedBar


@dataclass
class DirectionState:
    """一侧持仓的独立状态；不包含策略参数默认值。"""

    quantity: Decimal = Decimal(0)
    average_price: Decimal = Decimal(0)
    unit_quantity: Decimal = Decimal(0)
    position_id: object | None = None
    position_start_ns: int | None = None
    cooldown_until_ns: int = 0
    entry_order_id: object | None = None
    take_profit_order_id: object | None = None
    stop_order_id: object | None = None
    last_initial_fill_ns: int | None = None
    last_initial_fill_price: Decimal | None = None
    cycle_risk_budget: Decimal = Decimal(0)
    cycle_entry_fees: Decimal = Decimal(0)
    ma52_recovery_waiting: bool = False
    ma52_recovery_started_ns: int | None = None
    ma52_recovery_released_ns: int | None = None
    daily_ma52_recovery_confirmed: bool = True
    daily_ma52_risk_zone_active: bool = False
    hard_stop_count: int = 0
    fill_count: int = 0
    cycle_ma52_snapshot: list[CompletedBar] = field(default_factory=list)

    @property
    def direction(self) -> int:
        """固定做多方向。"""
        return 1

    @property
    def invested(self) -> bool:
        return self.quantity > 0

    @property
    def units(self) -> Decimal:
        if self.unit_quantity <= 0:
            return Decimal(0)
        return self.quantity / self.unit_quantity

    def apply_entry(self, quantity: Decimal, price: Decimal, fee: Decimal) -> None:
        old_quantity = self.quantity
        new_quantity = old_quantity + quantity
        self.average_price = (old_quantity * self.average_price + quantity * price) / new_quantity
        self.quantity = new_quantity
        self.cycle_entry_fees += fee
        self.fill_count += 1

    def apply_exit(self, quantity: Decimal) -> None:
        self.quantity = max(Decimal(0), self.quantity - quantity)
        self.fill_count += 1

    def clear_cycle(self) -> None:
        """清理持仓周期，但保留冷却和硬止损恢复锁。"""
        self.quantity = Decimal(0)
        self.average_price = Decimal(0)
        self.unit_quantity = Decimal(0)
        self.position_id = None
        self.position_start_ns = None
        self.entry_order_id = None
        self.take_profit_order_id = None
        self.stop_order_id = None
        self.last_initial_fill_ns = None
        self.last_initial_fill_price = None
        self.cycle_risk_budget = Decimal(0)
        self.cycle_entry_fees = Decimal(0)
        self.cycle_ma52_snapshot.clear()
