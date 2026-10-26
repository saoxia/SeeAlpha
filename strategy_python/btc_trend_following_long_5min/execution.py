"""把 MA20 趋势目标转换为做多突破和减仓订单。"""
from dataclasses import dataclass
from decimal import ROUND_DOWN, ROUND_UP, Decimal
from typing import Protocol, cast

from nautilus_trader.model import (
    ClientOrderId,
    OrderCanceled,
    OrderDenied,
    OrderExpired,
    OrderFilled,
    OrderModifyRejected,
    OrderRejected,
    OrderSide,
    OrderUpdated,
    PositionId,
    TimeInForce,
    TriggerType,
)

from nautilus_support import Instrument, Order, make_strategy_price, strategy_cache
from strategy_python._strategy import Strategy
from strategy_python.btc_trend_following_long_5min.state import TrendLegState


@dataclass(frozen=True)
class OrderMeta:
    direction: int
    role: str
    slot: int = 0


class _Owner(Protocol):
    id: object
    instrument: Instrument | None
    legs: tuple[TrendLegState, ...]
    config: object
    cache: object
    order_factory: object

    def submit_order(self, order: Order, position_id: object | None = None) -> None: ...
    def modify_order(self, order: Order, **kwargs: object) -> None: ...
    def cancel_order(self, order: Order) -> None: ...
    def _floor_quantity(self, value: Decimal) -> Decimal: ...


class TrendExecutionMixin:
    def _owner(self) -> _Owner:
        return cast(_Owner, self)

    def _strategy(self) -> Strategy:
        return cast(Strategy, self)

    def _state_for_meta(self, meta: OrderMeta) -> TrendLegState:
        owner = self._owner()
        return owner.legs[meta.slot]

    def _initialize_execution(self) -> None:
        self._order_meta: dict[ClientOrderId, OrderMeta] = {}
        self._stopping = False

    def _reset_execution(self) -> None:
        self._order_meta.clear()
        self._stopping = False

    def _sync_stop(self, state: TrendLegState) -> None:
        owner = self._owner()
        if (
            self._stopping
            or not owner.config.native_stop_loss
            or owner.config.max_loss_return is None
            or not state.invested
            or state.average_entry_price is None
            or owner.instrument is None
        ):
            return
        strategy = self._strategy()
        trigger = state.average_entry_price * (
            Decimal(1) - state.direction * Decimal(str(owner.config.max_loss_return))
        )
        if owner.config.peak_profit_exit and state.peak_stop_price is not None:
            trigger = (
                max(trigger, state.peak_stop_price)
                if state.direction > 0
                else min(trigger, state.peak_stop_price)
            )
        tick = owner.instrument.price_increment.as_decimal()
        rounding = ROUND_UP if state.direction > 0 else ROUND_DOWN
        trigger = (trigger / tick).to_integral_value(rounding=rounding) * tick
        price = make_strategy_price(owner.instrument, trigger)
        current = self._cached_order(state.stop_order_id)
        if current is not None:
            if current.is_pending_cancel or current.is_pending_update:
                return
            quantity = owner.instrument.make_qty(state.quantity + current.filled_qty.as_decimal())
            if current.quantity != quantity or current.trigger_price != price:
                strategy.modify_order(current.client_order_id, quantity=quantity, trigger_price=price)
            return
        order = strategy.order_factory.stop_market(
            instrument_id=owner.config.instrument_id,
            order_side=OrderSide.SELL if state.direction > 0 else OrderSide.BUY,
            quantity=owner.instrument.make_qty(state.quantity),
            trigger_price=price,
            trigger_type=TriggerType.LAST_PRICE,
            time_in_force=TimeInForce.GTC,
            reduce_only=True,
            tags=[f"btc_trend_following_long_5min:stop_loss:{state.direction}"],
        )
        state.stop_order_id = order.client_order_id
        self._order_meta[order.client_order_id] = OrderMeta(
            state.direction, "stop_loss", state.slot
        )
        strategy.submit_order(order, position_id=state.position_id)

    def _cancel_own_orders(self) -> None:
        # Venue-wide bulk cancellation can affect another strategy on the same instrument.
        self._strategy().cancel_all_orders(self._owner().config.instrument_id, strategy_only=True)

    def _cancel_leg_orders(self, state: TrendLegState, exclude: object) -> None:
        for order_id in (state.entry_order_id, state.exit_order_id, state.stop_order_id):
            if order_id == exclude:
                continue
            order = self._cached_order(order_id)
            if order is not None and not order.is_pending_cancel:
                self._strategy().cancel_order(order.client_order_id)

    def upsert_entry(self, state: TrendLegState, quantity: Decimal, price: Decimal) -> None:
        owner = self._owner()
        strategy = self._strategy()
        if owner.instrument is None or quantity <= 0:
            self.cancel_entry(state)
            return
        quantity_obj = owner.instrument.make_qty(quantity)
        price_obj = make_strategy_price(owner.instrument, price)
        current = self._cached_order(state.entry_order_id)
        if current is not None:
            if (
                current.quantity != quantity_obj
                or current.price != price_obj
                or current.trigger_price != price_obj
            ):
                strategy.modify_order(
                    current.client_order_id,
                    quantity=quantity_obj,
                    price=price_obj,
                    trigger_price=price_obj,
                )
            return

        side = OrderSide.BUY if state.direction > 0 else OrderSide.SELL
        order = strategy.order_factory.stop_limit(
            instrument_id=owner.config.instrument_id,
            order_side=side,
            quantity=quantity_obj,
            price=price_obj,
            trigger_price=price_obj,
            time_in_force=TimeInForce.GTC,
            tags=[f"btc_trend_following_long_5min:entry:{state.direction}"],
        )
        state.entry_order_id = order.client_order_id
        self._order_meta[order.client_order_id] = OrderMeta(state.direction, "entry", state.slot)
        if owner.config.max_positions_per_side > 1:
            # Explicit IDs keep same-direction entries as separate hedged positions.
            state.position_id = PositionId(f"{owner.strategy_id}-{order.client_order_id}")
            strategy.submit_order(order, position_id=state.position_id)
        else:
            strategy.submit_order(order)

    def submit_entry_market(self, state: TrendLegState, quantity: Decimal) -> None:
        owner = self._owner()
        if (
            owner.instrument is None
            or quantity <= 0
            or state.invested
            or self._cached_order(state.entry_order_id) is not None
        ):
            return
        strategy = self._strategy()
        order = strategy.order_factory.market(
            instrument_id=owner.config.instrument_id,
            order_side=OrderSide.BUY if state.direction > 0 else OrderSide.SELL,
            quantity=owner.instrument.make_qty(quantity),
            time_in_force=TimeInForce.IOC,
            tags=[f"btc_trend_following_long_5min:entry:{state.direction}"],
        )
        state.entry_order_id = order.client_order_id
        self._order_meta[order.client_order_id] = OrderMeta(state.direction, "entry", state.slot)
        if owner.config.max_positions_per_side > 1:
            state.position_id = PositionId(f"{owner.strategy_id}-{order.client_order_id}")
            strategy.submit_order(order, position_id=state.position_id)
        else:
            strategy.submit_order(order)

    def submit_exit(self, state: TrendLegState, *, half: bool) -> None:
        owner = self._owner()
        strategy = self._strategy()
        if (
            owner.instrument is None
            or not state.invested
            or state.position_id is None
            or self._cached_order(state.exit_order_id) is not None
        ):
            return
        quantity = state.quantity
        if half:
            quantity = owner._floor_quantity(state.quantity / 2)
        if quantity <= 0:
            return
        side = OrderSide.SELL if state.direction > 0 else OrderSide.BUY
        role = "exit_half" if half else "exit_all"
        order = strategy.order_factory.market(
            instrument_id=owner.config.instrument_id,
            order_side=side,
            quantity=owner.instrument.make_qty(quantity),
            time_in_force=TimeInForce.IOC,
            reduce_only=True,
            tags=[f"btc_trend_following_long_5min:{role}:{state.direction}"],
        )
        state.exit_order_id = order.client_order_id
        self._order_meta[order.client_order_id] = OrderMeta(state.direction, role, state.slot)
        strategy.submit_order(order, position_id=state.position_id)

    def cancel_entry(self, state: TrendLegState) -> None:
        order = self._cached_order(state.entry_order_id)
        if order is not None and not order.is_pending_cancel:
            self._strategy().cancel_order(order.client_order_id)

    def on_order_filled(self, event: OrderFilled) -> None:
        meta = self._order_meta.get(event.client_order_id)
        if meta is None:
            return
        state = self._state_for_meta(meta)
        order = strategy_cache(self._strategy()).order(event.client_order_id)
        quantity = event.last_qty.as_decimal()
        if meta.role == "entry":
            state.record_entry_fill(quantity, event.last_px.as_decimal())
            state.last_entry_fill_ns = event.ts_event
            state.position_id = event.position_id
            if order is not None and order.is_closed:
                state.entry_order_id = None
            self._sync_stop(state)
        else:
            state.quantity = max(Decimal(0), state.quantity - quantity)
            if meta.role == "exit_half":
                state.first_exit_done = True
            if state.exit_order_id == event.client_order_id and (order is None or order.is_closed):
                state.exit_order_id = None
            if state.stop_order_id == event.client_order_id and (order is None or order.is_closed):
                state.stop_order_id = None
            if state.quantity <= self._owner().instrument.size_increment.as_decimal() / 2:
                self._cancel_leg_orders(state, exclude=event.client_order_id)
                state.clear()
                state.lock_reentry(self._owner().config.cooldown_bars)
                siblings = self._owner().legs
                if not any(other.invested for other in siblings):
                    # A spare slot must not bypass the original flat-direction rearm rule.
                    for other in siblings:
                        self.cancel_entry(other)
                        other.lock_reentry(self._owner().config.cooldown_bars)
                if meta.role == "stop_loss":
                    state.stop_cooldown_until_ns = (
                        event.ts_event
                        + self._owner().config.stop_loss_cooldown_seconds * 1_000_000_000
                    )
            else:
                if meta.role == "stop_loss":
                    self.cancel_entry(state)
                self._sync_stop(state)
        if order is not None and order.is_closed:
            self._order_meta.pop(event.client_order_id, None)

    def on_order_canceled(self, event: OrderCanceled) -> None:
        self._forget_order(event.client_order_id)

    def on_order_expired(self, event: OrderExpired) -> None:
        self._forget_order(event.client_order_id)

    def on_order_denied(self, event: OrderDenied) -> None:
        self._forget_order(event.client_order_id)

    def on_order_rejected(self, event: OrderRejected) -> None:
        self._forget_order(event.client_order_id)

    def on_order_modify_rejected(self, event: OrderModifyRejected) -> None:
        meta = self._order_meta.get(event.client_order_id)
        if meta is not None and meta.role == "stop_loss" and not self._stopping:
            state = self._state_for_meta(meta)
            self.cancel_entry(state)
            self.submit_exit(state, half=False)

    def on_order_updated(self, event: OrderUpdated) -> None:
        meta = self._order_meta.get(event.client_order_id)
        if meta is not None and meta.role == "stop_loss":
            state = self._state_for_meta(meta)
            if state.stop_order_id == event.client_order_id:
                self._sync_stop(state)

    def _forget_order(self, order_id: ClientOrderId) -> None:
        meta = self._order_meta.pop(order_id, None)
        if meta is None:
            return
        state = self._state_for_meta(meta)
        if state.entry_order_id == order_id:
            state.entry_order_id = None
        if state.exit_order_id == order_id:
            state.exit_order_id = None
        if state.stop_order_id == order_id:
            state.stop_order_id = None
            if state.invested and not self._stopping:
                # Unexpected cancellation/rejection must not leave an unprotected position.
                self.cancel_entry(state)
                self.submit_exit(state, half=False)

    def _cached_order(self, order_id: object | None) -> Order | None:
        if order_id is None:
            return None
        cache = strategy_cache(self._strategy())
        if cache is None:
            return None
        order = cache.order(order_id)
        return order if order is not None and not order.is_closed else None
