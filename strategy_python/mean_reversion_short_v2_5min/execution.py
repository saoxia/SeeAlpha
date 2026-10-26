"""单份空仓的市价入场、原生止损/止盈及限时市价退出。"""
from decimal import ROUND_DOWN, Decimal

from nautilus_trader.model import (
    ContingencyType,
    OrderSide,
    OrderType,
    PositionId,
    TimeInForce,
    TriggerType,
)

from nautilus_support import make_strategy_price
from strategy_python.mean_reversion_short_v2_5min.state import TradeState


class MeanReversionExecutionMixin:
    def initialize_execution(self):
        self.trade = TradeState()
        self._roles = {}
        self._stopping = False
        self.last_exit_ns = 0

    def active_order(self, identifier):
        order = self.cache.order(identifier) if identifier is not None else None
        return order if order is not None and not order.is_closed else None

    def execution_busy(self):
        return self.trade.quantity > 0 or any(
            self.active_order(identifier) is not None for identifier in self._roles
        )

    def enter_short(self, quantity: Decimal, stop: Decimal, target: Decimal):
        if self._stopping or quantity <= 0 or self.execution_busy():
            return
        tick = self.instrument.price_increment.as_decimal()
        self.trade = TradeState(
            stop=(stop / tick).to_integral_value(rounding=ROUND_DOWN) * tick,
            target=(target / tick).to_integral_value(rounding=ROUND_DOWN) * tick,
        )
        orders = self.order_factory.bracket(
            instrument_id=self.config.instrument_id,
            order_side=OrderSide.SELL,
            quantity=self.instrument.make_qty(quantity),
            entry_order_type=OrderType.MARKET,
            time_in_force=TimeInForce.IOC,
            contingency_type=ContingencyType.OUO,
            tp_price=make_strategy_price(self.instrument, self.trade.target),
            tp_post_only=False,
            sl_trigger_price=make_strategy_price(self.instrument, self.trade.stop),
            sl_trigger_type=TriggerType.LAST_PRICE,
            entry_tags=["mean_reversion_short_v2_5min:entry"],
            tp_tags=["mean_reversion_short_v2_5min:target"],
            sl_tags=["mean_reversion_short_v2_5min:stop"],
        )
        entry, target, stop = orders
        self.trade.entry_id, self.trade.target_id, self.trade.stop_id = (
            entry.client_order_id, target.client_order_id, stop.client_order_id)
        self.trade.position_id = PositionId(f"{self.strategy_id}-{entry.client_order_id}")
        self._roles.update({entry.client_order_id:"entry", target.client_order_id:"target",
                            stop.client_order_id:"stop"})
        self.submit_order_list(orders, position_id=self.trade.position_id)

    def exit_short(self, reason: str):
        state = self.trade
        state.exiting = True
        for identifier in (state.entry_id, state.stop_id, state.target_id):
            order = self.active_order(identifier)
            if order is not None and not order.is_pending_cancel:
                self.cancel_order(order.client_order_id)
        if state.quantity <= 0 or self.active_order(state.exit_id) is not None:
            return
        order = self.order_factory.market(
            instrument_id=self.config.instrument_id,
            order_side=OrderSide.BUY,
            quantity=self.instrument.make_qty(state.quantity),
            time_in_force=TimeInForce.IOC,
            reduce_only=True,
            tags=[f"mean_reversion_short_v2_5min:{reason}"],
        )
        state.exit_id = order.client_order_id
        self._roles[order.client_order_id] = "exit"
        self.submit_order(order, position_id=state.position_id)

    def sync_protection(self):
        state = self.trade
        if state.quantity <= 0 or state.exiting or self._stopping:
            return
        if self.last_price >= state.stop or self.last_price <= state.target:
            self.exit_short("protection_crossed")
            return
        # Native OTO/OUO manages child activation, fill quantities and sibling cancellation.

    def on_order_filled(self, event):
        role = self._roles.get(event.client_order_id)
        if role is None:
            return
        state = self.trade
        quantity = event.last_qty.as_decimal()
        order = self.cache.order(event.client_order_id)
        if role == "entry":
            state.quantity += quantity
            state.entry_value += quantity * event.last_px.as_decimal()
            state.position_id = event.position_id
            if not state.opened_ns:
                state.opened_ns = event.ts_event
            average = state.entry_value / state.quantity
            if state.stop <= average or not 0 < state.target < average:
                state.exiting = True
            if state.exiting or self._stopping:
                self.exit_short("invalid_or_late_entry")
            else:
                self.sync_protection()
        else:
            average = state.entry_value / state.quantity if state.quantity else Decimal(0)
            state.quantity = max(Decimal(0), state.quantity - quantity)
            state.entry_value = average * state.quantity
            if state.quantity == 0:
                state.exiting = True
                self.last_exit_ns = event.ts_event
            elif role == "stop":
                self.exit_short("partial_stop")
            elif not state.exiting:
                self.sync_protection()
        if order is not None and order.is_closed:
            self._roles.pop(event.client_order_id, None)

    def forget_order(self, event):
        role = self._roles.pop(event.client_order_id, None)
        if role is None:
            return
        attribute = {
            "entry": "entry_id",
            "stop": "stop_id",
            "target": "target_id",
            "exit": "exit_id",
        }[role]
        if getattr(self.trade, attribute) == event.client_order_id:
            setattr(self.trade, attribute, None)
        if role in ("stop", "target") and self.trade.quantity > 0 and not self.trade.exiting:
            self.exit_short("protection_failed")

    on_order_canceled = forget_order
    on_order_rejected = forget_order
    on_order_denied = forget_order
    on_order_expired = forget_order

    def on_order_modify_rejected(self, event):
        if self._roles.get(event.client_order_id) in ("stop", "target"):
            self.exit_short("protection_modify_failed")

    def on_order_updated(self, event):
        if self._roles.get(event.client_order_id) in ("stop", "target"):
            self.sync_protection()

    def cancel_own_orders(self):
        self.cancel_all_orders(self.config.instrument_id, strategy_only=True)
