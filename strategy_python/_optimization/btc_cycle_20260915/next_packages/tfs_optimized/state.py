"""MA20 趋势策略单侧持仓的运行期状态。"""

from dataclasses import dataclass
from decimal import Decimal

from .profit_protection import peak_profit_lock_ratio


@dataclass
class TrendLegState:
    direction: int
    slot: int = 0
    quantity: Decimal = Decimal(0)
    position_id: object | None = None
    entry_order_id: object | None = None
    exit_order_id: object | None = None
    first_exit_done: bool = False
    first_exit_level: Decimal | None = None
    final_exit_level: Decimal | None = None
    cooldown_bars_remaining: int = 0
    rearmed: bool = True
    average_entry_price: Decimal | None = None
    dynamic_exit_armed: bool = False
    stop_order_id: object | None = None
    stop_cooldown_until_ns: int = 0
    last_entry_fill_ns: int = 0
    peak_price: Decimal | None = None
    peak_stop_price: Decimal | None = None

    def stop_cooldown_active(self, now_ns: int) -> bool:
        return now_ns < self.stop_cooldown_until_ns

    def __post_init__(self) -> None:
        if self.direction not in (-1, 1):
            raise ValueError("方向只能是 1（多）或 -1（空）")

    @property
    def invested(self) -> bool:
        return self.quantity > 0

    def record_entry_fill(self, quantity: Decimal, price: Decimal) -> None:
        """以实际成交量加权；减仓不改变剩余仓位的开仓成本价。"""
        if quantity <= 0 or price <= 0:
            raise ValueError("成交数量和价格必须为正")
        total = self.quantity + quantity
        previous = self.average_entry_price if self.average_entry_price is not None else price
        self.average_entry_price = (previous * self.quantity + price * quantity) / total
        self.quantity = total

    def ratchet_exits(self, first: Decimal, final: Decimal) -> None:
        """保护线只能朝盈利方向移动，不能因 MA 回撤而放松。"""
        if self.direction > 0:
            self.first_exit_level = max(self.first_exit_level or first, first)
            self.final_exit_level = max(self.final_exit_level or final, final)
        else:
            self.first_exit_level = min(self.first_exit_level or first, first)
            self.final_exit_level = min(self.final_exit_level or final, final)

    def update_profit_peak(
        self,
        price: Decimal,
        activation: Decimal,
        lock: Decimal,
        *,
        dynamic: bool = False,
    ) -> None:
        if not self.invested or self.average_entry_price is None:
            return
        entry = self.average_entry_price
        previous = self.peak_price if self.peak_price is not None else entry
        self.peak_price = max(previous, price) if self.direction > 0 else min(previous, price)
        # [计算来源] 有利移动/实际成交均价是毛浮盈；启动门槛不含杠杆或手续费。
        # 峰值由策略传入当时可见的完整持有分钟价格，计算不引用未来行情。
        profit = self.direction * (self.peak_price - entry)
        if dynamic:
            lock = Decimal(str(peak_profit_lock_ratio(float(profit / entry))))
        if profit >= entry * activation:
            # 锁住毛浮盈的lock份额：P_stop=P_entry+(P_peak-P_entry)*lock。
            # 例如4%×65%=2.6%是机械推导结果；4%与65%本身仍需选择/校准。
            trigger = entry + (self.peak_price - entry) * lock
            if self.peak_stop_price is None:
                self.peak_stop_price = trigger
            else:
                self.peak_stop_price = (
                    max(self.peak_stop_price, trigger)
                    if self.direction > 0
                    else min(self.peak_stop_price, trigger)
                )

    def clear(self) -> None:
        self.quantity = Decimal(0)
        self.position_id = None
        self.entry_order_id = None
        self.exit_order_id = None
        self.first_exit_done = False
        self.first_exit_level = None
        self.final_exit_level = None
        self.average_entry_price = None
        self.dynamic_exit_armed = False
        self.last_entry_fill_ns = 0
        self.peak_price = None
        self.peak_stop_price = None
        self.stop_order_id = None

    def lock_reentry(self, cooldown_bars: int) -> None:
        self.cooldown_bars_remaining = cooldown_bars
        self.rearmed = False

    def observe_reentry_zone(
        self,
        close: Decimal,
        ma: Decimal,
        std: Decimal,
        multiplier: Decimal,
    ) -> bool:
        if self.cooldown_bars_remaining > 0:
            self.cooldown_bars_remaining -= 1
        if abs(close - ma) <= multiplier * std:
            self.rearmed = True
        return self.cooldown_bars_remaining == 0 and self.rearmed
