"""两份仓位的固定比例止损风险定仓与止损后等待。"""
from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal
from math import isfinite

from strategy_python.btc_mean_reversion_short_5min.bars import CompletedBar
from strategy_python.btc_mean_reversion_short_5min.position import fixed_stop_price
from strategy_python.btc_mean_reversion_short_5min.state import DirectionState


@dataclass(frozen=True)
class RiskParameters:
    risk_fraction: float = .30
    max_units: int = 2
    entry_fee_rate: float = .0002
    forced_exit_fee_rate: float = .0005
    stop_cost_buffer: float = .05


class DirectionalRiskModel:
    def __init__(self, parameters: RiskParameters) -> None:
        self.parameters = parameters

    def _cost(self, quantity: Decimal, average: Decimal, stop: Decimal,
              entry_fees: Decimal) -> Decimal:
        loss = max(Decimal(0), stop - average) * quantity
        exit_fee = quantity * stop * Decimal(str(self.parameters.forced_exit_fee_rate))
        return (loss + entry_fees + exit_fee) * Decimal(str(1 + self.parameters.stop_cost_buffer))

    def size_initial_entry(self, direction: int, initial_price: float, now_ns: int,
                           equity: Decimal, size_increment: Decimal, *,
                           second_entry_price: float) -> dict | None:
        if direction != -1:
            raise ValueError("本策略仅允许做空风险定仓")
        values = (initial_price, second_entry_price)
        if any(v is None or not isfinite(v) for v in values):
            return None
        if initial_price <= 0 or second_entry_price <= 0 or equity <= 0 or size_increment <= 0:
            return None
        first, second = (Decimal(str(v)) for v in values)
        prices = [first, second][:self.parameters.max_units]
        average = sum(prices) / len(prices)
        stop = fixed_stop_price(average)
        initial_stop = fixed_stop_price(first)
        if initial_stop <= first:
            return None
        fee_rate = Decimal(str(self.parameters.entry_fee_rate))
        # 同时覆盖仅首仓止损和两份成交后止损，保持同一份额。
        cost = max(self._cost(Decimal(len(prices)), average, stop, sum(prices) * fee_rate),
                   self._cost(Decimal(1), first, initial_stop, first * fee_rate))
        available = equity * Decimal(str(self.parameters.risk_fraction))
        quantity = (available / cost / size_increment).to_integral_value(rounding=ROUND_DOWN) * size_increment
        if quantity <= 0:
            return None
        return {"unit_quantity": quantity, "risk_per_quantity": cost,
                "planned_risk": quantity * cost,
                "risk_context": {"risk_fraction": self.parameters.risk_fraction}}

    def entry_preserves_cycle_risk(self, state: DirectionState, quantity: Decimal,
                                   price: Decimal) -> bool:
        if not state.invested or quantity <= 0 or price <= 0:
            return False
        total = state.quantity + quantity
        average = (state.quantity * state.average_price + quantity * price) / total
        stop = fixed_stop_price(average)
        if stop <= price:
            return False
        fees = state.cycle_entry_fees + quantity * price * Decimal(str(self.parameters.entry_fee_rate))
        return self._cost(total, average, stop, fees) <= state.cycle_risk_budget + Decimal('.01')


class RecoveryModel:
    """止损后依次等待下方五根、上方五根、再下方五根。"""

    @staticmethod
    def reset_after_hard_stop(state: DirectionState, now_ns: int) -> None:
        state.hard_stop_count += 1
        state.reentry_waiting = True
        state.reentry_started_ns = now_ns
        state.reentry_stage = 0
        state.reentry_stage_count = 0
        state.reentry_last_bar_ns = None

    @staticmethod
    def on_five_minute_bar(state: DirectionState, bar: CompletedBar, ma99: float | None) -> bool:
        if not state.reentry_waiting or state.invested or state.reentry_started_ns is None:
            return False
        if bar.ts_start < state.reentry_started_ns:
            return False  # 止损发生所在的未完整后续 K 线不计数。
        last = state.reentry_last_bar_ns
        if last is not None and bar.ts_end <= last:
            return False
        if last is not None and bar.ts_start != last:
            state.reentry_stage = 0
            state.reentry_stage_count = 0
        state.reentry_last_bar_ns = bar.ts_end
        if (ma99 is None or not isfinite(ma99)
                or not isfinite(bar.high) or not isfinite(bar.low)):
            state.reentry_stage = 0
            state.reentry_stage_count = 0
            return False
        qualifies = bar.low > ma99 if state.reentry_stage == 1 else bar.high < ma99
        if not qualifies:
            state.reentry_stage_count = 0
            return False
        state.reentry_stage_count += 1
        if state.reentry_stage_count == 5:
            if state.reentry_stage == 2:
                state.reentry_waiting = False
                return True
            state.reentry_stage += 1
            state.reentry_stage_count = 0
        return False
