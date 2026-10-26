"""单份空仓的市价入场、原生止损/止盈及限时市价退出。"""
from decimal import ROUND_DOWN, Decimal

from nautilus_trader.model import OrderSide, PositionId, TimeInForce, TriggerType

from nautilus_support import make_strategy_price
from strategy_python.btc_scaled_pullback_short_1h.state import TradeState


class PullbackExecutionMixin:
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

    def enter_short(self, quantity: Decimal, stop: Decimal):
        if self._stopping or quantity <= 0 or self.execution_busy():
            return
        tick = self.instrument.price_increment.as_decimal()
        self.trade = TradeState(stop=(stop / tick).to_integral_value(rounding=ROUND_DOWN) * tick)
        order = self.order_factory.market(
            instrument_id=self.config.instrument_id,
            order_side=OrderSide.SELL,
            quantity=self.instrument.make_qty(quantity),
            time_in_force=TimeInForce.IOC,
            tags=["btc_scaled_pullback_short_1h:entry"],
        )
        self.trade.entry_id = order.client_order_id
        self.trade.position_id = PositionId(f"{self.strategy_id}-{order.client_order_id}")
        self._roles[order.client_order_id] = "entry"
        self.submit_order(order, position_id=self.trade.position_id)

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
            tags=[f"btc_scaled_pullback_short_1h:{reason}"],
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
        for role, attribute, level in (
            ("stop", "stop_id", state.stop),
            ("target", "target_id", state.target),
        ):
            if state.exiting or state.quantity <= 0:
                break
            price = make_strategy_price(self.instrument, level)
            current = self.active_order(getattr(state, attribute))
            if current is not None:
                if current.is_pending_cancel or current.is_pending_update:
                    continue
                qty = self.instrument.make_qty(state.quantity + current.filled_qty.as_decimal())
                key = "trigger_price" if role == "stop" else "price"
                if current.quantity != qty or getattr(current, key) != price:
                    self.modify_order(current.client_order_id, quantity=qty, **{key: price})
                continue
            args = {
                "instrument_id": self.config.instrument_id,
                "order_side": OrderSide.BUY,
                "quantity": self.instrument.make_qty(state.quantity),
                "time_in_force": TimeInForce.GTC,
                "reduce_only": True,
                "tags": [f"btc_scaled_pullback_short_1h:{role}"],
            }
            if role == "stop":
                order = self.order_factory.stop_market(
                    **args, trigger_price=price, trigger_type=TriggerType.LAST_PRICE
                )
            else:
                order = self.order_factory.limit(**args, price=price)
            setattr(state, attribute, order.client_order_id)
            self._roles[order.client_order_id] = role
            self.submit_order(order, position_id=state.position_id)

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
            state.target = average - Decimal(str(self.config.reward_risk)) * (state.stop - average)
            if state.stop <= average or state.target <= 0:
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
                for identifier in (state.entry_id, state.stop_id, state.target_id, state.exit_id):
                    other = self.active_order(identifier)
                    if (
                        other is not None
                        and identifier != event.client_order_id
                        and not other.is_pending_cancel
                    ):
                        self.cancel_order(other.client_order_id)
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
