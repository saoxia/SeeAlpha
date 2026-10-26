"""ETHUSDT v4 的 Nautilus 订单执行和成交状态同步。"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol, cast

from nautilus_trader.model.data import Bar
from nautilus_trader.model.enums import OrderSide, TimeInForce
from nautilus_trader.model.events import (
    OrderCanceled,
    OrderDenied,
    OrderExpired,
    OrderFilled,
    OrderModifyRejected,
    OrderRejected,
    OrderUpdated,
)
from nautilus_trader.model.identifiers import ClientOrderId
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.model.orders import Order
from nautilus_trader.trading.strategy import Strategy

from strategy_python._mechanism.rules import MeanReversionSnapshot
from strategy_python._optimization.btc_cycle_20260915.packages.mrl_original.bars import (
    NANOSECONDS_PER_MINUTE,
    CompletedBar,
)
from strategy_python._optimization.btc_cycle_20260915.packages.mrl_original.config import (
    MechanismMeanReversionLong5MinConfig,
)
from strategy_python._optimization.btc_cycle_20260915.packages.mrl_original.position import (
    EntryTarget,
)
from strategy_python._optimization.btc_cycle_20260915.packages.mrl_original.risk import (
    RecoveryModel,
)
from strategy_python._optimization.btc_cycle_20260915.packages.mrl_original.state import (
    DirectionState,
)


@dataclass
class OrderMeta:
    direction: int
    role: str
    quantity: Decimal
    risk_per_quantity: Decimal = Decimal(0)
    planned_risk: Decimal = Decimal(0)
    risk_context: dict[str, float] | None = None
    snapshot: MeanReversionSnapshot | None = None


class _ExecutionOwner(Protocol):
    config: MechanismMeanReversionLong5MinConfig
    instrument: Instrument | None
    trade_state: DirectionState
    recovery_model: RecoveryModel
    _six_hour_history: list[CompletedBar]

    def _size_increment(self) -> Decimal: ...

    def _floor_quantity(self, value: Decimal) -> Decimal: ...


class EthExecutionMixin:
    """把订单创建、维护和成交回写从策略编排层中分离。"""

    def _owner(self) -> _ExecutionOwner:
        return cast(_ExecutionOwner, self)

    def _strategy(self) -> Strategy:
        return cast(Strategy, self)

    def _initialize_execution(self) -> None:
        self._order_meta: dict[ClientOrderId, OrderMeta] = {}
        self._pending_entry_meta: dict[ClientOrderId, OrderMeta] = {}

    def _reset_execution(self) -> None:
        self._order_meta.clear()
        self._pending_entry_meta.clear()

    def _committed_cycle_risk(self) -> Decimal:
        """完整周期预留额度，包括未成交及等待撤销的入场单。"""
        committed = self._owner().trade_state.cycle_risk_budget
        for order_id, meta in {**self._order_meta, **self._pending_entry_meta}.items():
            if meta.role in ("initial", "add") and self._cached_order(order_id) is not None:
                committed = max(committed, meta.planned_risk)
        return committed

    def apply_entry_target(self, state: DirectionState, target: EntryTarget) -> None:
        """把风险层批准的入场目标转换为限价单。"""
        if target.direction != 1:
            raise ValueError("本策略仅允许做多入场")
        self._upsert_entry(
            state,
            role=target.role,
            quantity=target.quantity,
            price=target.price,
            risk_per_quantity=target.risk_per_quantity,
            planned_risk=target.planned_risk,
            risk_context=target.risk_context,
            snapshot=target.snapshot,
        )

    def _upsert_entry(
        self,
        state: DirectionState,
        role: str,
        quantity: Decimal,
        price: Decimal,
        risk_per_quantity: Decimal = Decimal(0),
        planned_risk: Decimal = Decimal(0),
        risk_context: dict[str, float] | None = None,
        snapshot: MeanReversionSnapshot | None = None,
    ) -> None:
        owner = self._owner()
        strategy = self._strategy()
        if owner.instrument is None or quantity <= 0:
            return
        quantity_obj = owner.instrument.make_qty(quantity)
        price_obj = owner.instrument.make_price(price)
        current = self._cached_order(state.entry_order_id)
        meta = (
            self._order_meta.get(state.entry_order_id) if state.entry_order_id is not None else None
        )
        new_meta = OrderMeta(
            direction=state.direction,
            role=role,
            quantity=quantity,
            risk_per_quantity=risk_per_quantity,
            planned_risk=max(planned_risk, state.cycle_risk_budget),
            risk_context=risk_context,
            snapshot=snapshot,
        )
        if current is not None and meta is not None and meta.role == role:
            if current.client_order_id in self._pending_entry_meta:
                return
            if current.quantity != quantity_obj or current.price != price_obj:
                self._pending_entry_meta[current.client_order_id] = new_meta
                strategy.modify_order(current, quantity=quantity_obj, price=price_obj)
            else:
                self._order_meta[current.client_order_id] = new_meta
            return
        self._cancel_entry(state)
        side = OrderSide.BUY
        order = strategy.order_factory.limit(
            instrument_id=owner.config.instrument_id,
            order_side=side,
            quantity=quantity_obj,
            price=price_obj,
            time_in_force=TimeInForce.GTC,
            tags=[f"ethusdt-mean-reversion-long:{role}:{state.direction}"],
        )
        state.entry_order_id = order.client_order_id
        self._order_meta[order.client_order_id] = new_meta
        strategy.submit_order(order, position_id=state.position_id if role == "add" else None)

    def _refresh_protection(self, state: DirectionState) -> None:
        owner = self._owner()
        if owner.instrument is None or not state.invested or state.position_id is None:
            return
        distance = owner.config.hard_stop_distance
        stop_price = state.average_price * Decimal(str(1 - state.direction * distance))
        if state.units > Decimal("2.000000001"):
            tp_price = state.average_price
            tp_quantity = owner._floor_quantity(state.quantity / 2)
        else:
            tp_price = state.average_price * Decimal(
                str(1 + state.direction * state.cycle_snapshot.take_profit_return),
            )
            tp_quantity = state.quantity
        self._upsert_exit(state, "stop", state.quantity, stop_price)
        self._upsert_exit(state, "take_profit", tp_quantity, tp_price)

    def _upsert_exit(
        self,
        state: DirectionState,
        role: str,
        quantity: Decimal,
        price: Decimal,
    ) -> None:
        owner = self._owner()
        strategy = self._strategy()
        if owner.instrument is None or quantity <= 0:
            return
        attribute = "take_profit_order_id" if role == "take_profit" else "stop_order_id"
        order_id = getattr(state, attribute)
        current = self._cached_order(order_id)
        quantity_obj = owner.instrument.make_qty(quantity)
        price_obj = owner.instrument.make_price(price)
        if current is not None:
            total_quantity = owner.instrument.make_qty(current.filled_qty.as_decimal() + quantity)
            if role == "take_profit":
                if current.quantity != total_quantity or current.price != price_obj:
                    strategy.modify_order(current, quantity=total_quantity, price=price_obj)
            elif current.quantity != total_quantity or current.trigger_price != price_obj:
                strategy.modify_order(current, quantity=total_quantity, trigger_price=price_obj)
            return
        side = OrderSide.SELL
        if role == "take_profit":
            order = strategy.order_factory.limit(
                instrument_id=owner.config.instrument_id,
                order_side=side,
                quantity=quantity_obj,
                price=price_obj,
                time_in_force=TimeInForce.GTC,
                reduce_only=True,
                tags=[f"ethusdt-mean-reversion-long:take-profit:{state.direction}"],
            )
        else:
            order = strategy.order_factory.stop_market(
                instrument_id=owner.config.instrument_id,
                order_side=side,
                quantity=quantity_obj,
                trigger_price=price_obj,
                time_in_force=TimeInForce.GTC,
                reduce_only=True,
                tags=[f"ethusdt-mean-reversion-long:hard-stop:{state.direction}"],
            )
        setattr(state, attribute, order.client_order_id)
        self._order_meta[order.client_order_id] = OrderMeta(
            direction=state.direction,
            role=role,
            quantity=quantity,
        )
        strategy.submit_order(order, position_id=state.position_id)

    def on_order_filled(self, event: OrderFilled) -> None:
        """以实际成交事件更新方向账本，并立即刷新保护单。"""
        owner = self._owner()
        strategy = self._strategy()
        meta = self._order_meta.get(event.client_order_id)
        if meta is None:
            return
        state = owner.trade_state
        quantity = event.last_qty.as_decimal()
        price = event.last_px.as_decimal()
        commission = event.commission.as_decimal()
        order = strategy.cache.order(event.client_order_id)
        if order is not None and order.is_closed:
            self._order_meta.pop(event.client_order_id, None)
            self._pending_entry_meta.pop(event.client_order_id, None)

        if meta.role in ("initial", "add"):
            was_flat = not state.invested
            state.apply_entry(quantity, price, commission)
            state.position_id = event.position_id
            if meta.role == "add" and was_flat:
                # A cancelled sibling can fill after its cycle has already closed.
                # Do not turn it into a new alpha entry or invent a new risk budget.
                state.cycle_snapshot = meta.snapshot
                state.unit_quantity = meta.quantity
                state.position_start_ns = event.ts_event
                self._cancel_state_orders(state, exclude=event.client_order_id)
                self._submit_emergency_exit(state, "late_add_after_flat", role="orphan_exit")
                return
            if meta.role == "initial" and was_flat:
                if meta.snapshot is None:
                    raise RuntimeError("Initial fill lacks a confirmed scale snapshot")
                state.cycle_snapshot = meta.snapshot
                state.unit_quantity = meta.quantity
                state.position_start_ns = event.ts_event
                state.cycle_risk_budget = meta.planned_risk
                state.cycle_ma52_snapshot = list(owner._six_hour_history)
                state.last_initial_fill_ns = event.ts_event
                state.last_initial_fill_price = price
            if order is not None and order.is_closed:
                state.entry_order_id = None
            self._refresh_protection(state)
            return

        state.apply_exit(quantity)
        if meta.role == "take_profit":
            state.take_profit_order_id = (
                None if order is None or order.is_closed else event.client_order_id
            )
        elif meta.role == "stop":
            state.stop_order_id = (
                None if order is None or order.is_closed else event.client_order_id
            )

        if state.quantity <= owner._size_increment() / 2:
            if meta.role in ("stop", "stop_emergency"):
                owner.recovery_model.reset_after_hard_stop(state, event.ts_event)
            elif meta.role in ("take_profit", "cycle_exit"):
                state.cooldown_until_ns = (
                    event.ts_event + owner.config.flat_cooldown_minutes * NANOSECONDS_PER_MINUTE
                )
            self._cancel_state_orders(state, exclude=event.client_order_id)
            state.clear_cycle()
        else:
            self._refresh_protection(state)

    def on_order_canceled(self, event: OrderCanceled) -> None:
        self._handle_closed_order(event.client_order_id)

    def on_order_expired(self, event: OrderExpired) -> None:
        self._handle_closed_order(event.client_order_id)

    def on_order_denied(self, event: OrderDenied) -> None:
        self._handle_failed_order(event.client_order_id)

    def on_order_rejected(self, event: OrderRejected) -> None:
        self._handle_failed_order(event.client_order_id)

    def on_order_updated(self, event: OrderUpdated) -> None:
        pending = self._pending_entry_meta.pop(event.client_order_id, None)
        if pending is not None and event.client_order_id in self._order_meta:
            self._order_meta[event.client_order_id] = pending

    def on_order_modify_rejected(self, event: OrderModifyRejected) -> None:
        self._pending_entry_meta.pop(event.client_order_id, None)
        meta = self._order_meta.get(event.client_order_id)
        if meta is not None and meta.role == "stop":
            state = self._owner().trade_state
            self._strategy().log.error(
                f"保护性止损单修改失败，提交应急平仓：{event.client_order_id}",
            )
            self._submit_emergency_exit(state, "stop_modify_failed")

    def _handle_failed_order(self, order_id: ClientOrderId) -> None:
        meta = self._order_meta.get(order_id)
        self._handle_closed_order(order_id)
        if meta is not None and meta.role == "stop":
            state = self._owner().trade_state
            self._strategy().log.error(f"保护性止损单失败，提交应急平仓：{order_id}")
            self._submit_emergency_exit(state, "stop_order_failed")

    def _handle_closed_order(self, order_id: ClientOrderId) -> None:
        self._pending_entry_meta.pop(order_id, None)
        meta = self._order_meta.pop(order_id, None)
        if meta is None:
            return
        state = self._owner().trade_state
        if state.entry_order_id == order_id:
            state.entry_order_id = None
        if state.take_profit_order_id == order_id:
            state.take_profit_order_id = None
        if state.stop_order_id == order_id:
            state.stop_order_id = None

    def _cached_order(self, order_id: object | None) -> Order | None:
        if order_id is None:
            return None
        order = self._strategy().cache.order(order_id)
        return order if order is not None and not order.is_closed else None

    def _cancel_entry(self, state: DirectionState) -> None:
        order = self._cached_order(state.entry_order_id)
        if order is not None and not order.is_pending_cancel:
            self._strategy().cancel_order(order)

    def _cancel_state_orders(
        self,
        state: DirectionState,
        exclude: ClientOrderId | None = None,
    ) -> None:
        for order_id in (
            state.entry_order_id,
            state.take_profit_order_id,
            state.stop_order_id,
        ):
            if order_id is None or order_id == exclude:
                continue
            order = self._cached_order(order_id)
            if order is not None and not order.is_pending_cancel:
                self._strategy().cancel_order(order)

    def _submit_emergency_exit(
        self, state: DirectionState, reason: str, *, role: str = "stop_emergency"
    ) -> None:
        owner = self._owner()
        strategy = self._strategy()
        if owner.instrument is None or not state.invested or state.position_id is None:
            return
        if any(
            meta.direction == state.direction and meta.role in ("stop_emergency", "orphan_exit", "cycle_exit")
            for meta in self._order_meta.values()
        ):
            return
        side = OrderSide.SELL
        order = strategy.order_factory.market(
            instrument_id=owner.config.instrument_id,
            order_side=side,
            quantity=owner.instrument.make_qty(state.quantity),
            time_in_force=TimeInForce.IOC,
            reduce_only=True,
            tags=[f"ethusdt-mean-reversion-long:emergency:{reason}"],
        )
        self._order_meta[order.client_order_id] = OrderMeta(
            direction=state.direction,
            role=role,
            quantity=state.quantity,
        )
        strategy.submit_order(order, position_id=state.position_id)

    def _certain_initial_fill_then_stop(self, state: DirectionState, bar: Bar) -> bool:
        owner = self._owner()
        if (
            not state.invested
            or state.last_initial_fill_ns != bar.ts_event
            or state.last_initial_fill_price is None
        ):
            return False
        entry = state.last_initial_fill_price
        distance = Decimal(
            str(owner.config.hard_stop_distance),
        )
        stop = state.average_price * (Decimal(1) - state.direction * distance)
        return bar.open.as_decimal() >= entry and bar.low.as_decimal() <= stop
