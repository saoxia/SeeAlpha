"""BTCUSDT v4 策略运行期间的可变状态。"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass
class DirectionState:
    """一侧持仓的独立状态；不包含策略参数默认值。"""

    phase: str = "initial"  # initial: 首仓；scaled: 已加仓；remainder: 首次止盈后的余仓
    quantity: Decimal = Decimal(0)
    average_price: Decimal = Decimal(0)
    last_entry_price: Decimal | None = None
    last_add_fill_ns: int | None = None
    no_lower_touch_exit_active: bool = False
    no_lower_touch_count: int = 0
    no_lower_touch_last_ns: int | None = None
    breakeven_exit_active: bool = False
    time_exit_loss_fraction: Decimal = Decimal(0)
    unit_quantity: Decimal = Decimal(0)
    position_id: object | None = None
    position_start_ns: int | None = None
    entry_ma99: Decimal | None = None
    add_distance_snapshot: Decimal | None = None
    emergency_exit_reason: str | None = None
    emergency_requires_reentry_wait: bool = False
    exit_started: bool = False
    second_take_profit_full_exit: bool = False
    entry_order_id: object | None = None
    take_profit_order_id: object | None = None
    second_take_profit_order_id: object | None = None
    stop_order_id: object | None = None
    last_initial_fill_ns: int | None = None
    last_initial_fill_price: Decimal | None = None
    cycle_risk_budget: Decimal = Decimal(0)
    cycle_entry_fees: Decimal = Decimal(0)
    reentry_waiting: bool = False
    reentry_started_ns: int | None = None
    reentry_stage: int = 0  # 0: 下方五根；1: 上方五根；2: 再下方五根
    reentry_stage_count: int = 0
    reentry_last_bar_ns: int | None = None
    stop_exit_started: bool = False
    hard_stop_count: int = 0
    fill_count: int = 0

    @property
    def direction(self) -> int:
        """固定做空方向。"""
        return -1

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
        self.last_entry_price = price
        self.quantity = new_quantity
        self.cycle_entry_fees += fee
        self.fill_count += 1

    def apply_exit(self, quantity: Decimal) -> None:
        self.quantity = max(Decimal(0), self.quantity - quantity)
        self.fill_count += 1

    def clear_cycle(self) -> None:
        """清理持仓周期，但保留止损统计和重新入场等待锁。"""
        self.phase = "initial"
        self.quantity = Decimal(0)
        self.average_price = Decimal(0)
        self.last_entry_price = None
        self.last_add_fill_ns = None
        self.no_lower_touch_exit_active = False
        self.no_lower_touch_count = 0
        self.no_lower_touch_last_ns = None
        self.breakeven_exit_active = False
        self.time_exit_loss_fraction = Decimal(0)
        self.unit_quantity = Decimal(0)
        self.position_id = None
        self.position_start_ns = None
        self.entry_ma99 = None
        self.add_distance_snapshot = None
        self.emergency_exit_reason = None
        self.emergency_requires_reentry_wait = False
        self.exit_started = False
        self.second_take_profit_full_exit = False
        self.stop_exit_started = False
        self.entry_order_id = None
        self.take_profit_order_id = None
        self.second_take_profit_order_id = None
        self.stop_order_id = None
        self.last_initial_fill_ns = None
        self.last_initial_fill_price = None
        self.cycle_risk_budget = Decimal(0)
        self.cycle_entry_fees = Decimal(0)
