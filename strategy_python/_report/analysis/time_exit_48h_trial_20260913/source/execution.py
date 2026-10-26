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

from strategy_python.mean_reversion_short_5min.config import MeanReversionShort5MinConfig
from strategy_python.mean_reversion_short_5min.position import EntryTarget
from strategy_python.mean_reversion_short_5min.risk import RecoveryModel
from strategy_python.mean_reversion_short_5min.state import DirectionState


@dataclass
class OrderMeta:
    direction: int
    role: str
    quantity: Decimal
    risk_per_quantity: Decimal = Decimal(0)
    planned_risk: Decimal = Decimal(0)
    risk_context: dict[str, float] | None = None
    invalidated: bool = False
    full_exit: bool = False
    stop_requires_reentry_wait: bool = False


class _ExecutionOwner(Protocol):
    config: MeanReversionShort5MinConfig
    instrument: Instrument | None
    trade_state: DirectionState
    recovery_model: RecoveryModel

    def _size_increment(self) -> Decimal: ...

    def _floor_quantity(self, value: Decimal) -> Decimal: ...

    def _take_profit_prices(self, state: DirectionState) -> tuple[Decimal, Decimal] | None: ...

    def _stop_target_price(self, state: DirectionState) -> Decimal | None: ...

    def _lock_entry_ma99(self, state: DirectionState) -> None: ...

    def _snapshot_add_distance(self, state: DirectionState) -> None: ...

    def _refresh_add_order(self, state: DirectionState, now_ns: int) -> None: ...


class EthExecutionMixin:
    """把订单创建、维护和成交回写从策略编排层中分离。"""

    def _owner(self) -> _ExecutionOwner:
        return cast(_ExecutionOwner, self)

    def _strategy(self) -> Strategy:
        return cast(Strategy, self)

    def _initialize_execution(self) -> None:
        self._order_meta: dict[ClientOrderId, OrderMeta] = {}
        self._market_price: Decimal | None = None
        self._market_price_ns: int = 0

    def _reset_execution(self) -> None:
        self._order_meta.clear()
        self._market_price = None
        self._market_price_ns = 0

    def _observe_market_price(self, price: Decimal, timestamp: int) -> None:
        if price.is_finite() and price > 0 and timestamp >= self._market_price_ns:
            self._market_price, self._market_price_ns = price, timestamp

    def _buy_market_price(self) -> Decimal | None:
        # Prefer a current ask when quotes exist; bar-only backtests use the latest
        # observed close or fill, never a candle's future high/low.
        quote_getter = getattr(self._strategy().cache, "quote_tick", None)
        quote = quote_getter(self._owner().config.instrument_id) if quote_getter else None
        if quote is not None and quote.ts_event >= self._market_price_ns:
            return quote.ask_price.as_decimal()
        return self._market_price

    def _committed_cycle_risk(self) -> Decimal:
        """完整周期预留额度，包括未成交及等待撤销的入场单。"""
        committed = self._owner().trade_state.cycle_risk_budget
        for order_id, meta in self._order_meta.items():
            if meta.role in ("initial", "add") and self._cached_order(order_id) is not None:
                committed = max(committed, meta.planned_risk)
        return committed

    def apply_entry_target(self, state: DirectionState, target: EntryTarget) -> None:
        """把风险层批准的入场目标转换为限价单。"""
        if target.direction != -1:
            raise ValueError("本策略仅允许做空入场")
        self._upsert_entry(
            state,
            role=target.role,
            quantity=target.quantity,
            price=target.price,
            risk_per_quantity=target.risk_per_quantity,
            planned_risk=target.planned_risk,
            risk_context=target.risk_context,
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
        )
        if current is not None and meta is not None and meta.role == role:
            if meta.invalidated or current.is_pending_cancel:
                return
            if role == "add":
                quantity_obj = owner.instrument.make_qty(current.filled_qty.as_decimal() + quantity)
            self._order_meta[current.client_order_id] = new_meta
            if current.quantity != quantity_obj or current.price != price_obj:
                strategy.modify_order(current, quantity=quantity_obj, price=price_obj)
            return
        self._cancel_entry(state)
        side = OrderSide.SELL
        order = strategy.order_factory.limit(
            instrument_id=owner.config.instrument_id,
            order_side=side,
            quantity=quantity_obj,
            price=price_obj,
            time_in_force=TimeInForce.GTC,
            tags=[f"ethusdt-mean-reversion-short:{role}:{state.direction}"],
        )
        state.entry_order_id = order.client_order_id
        self._order_meta[order.client_order_id] = new_meta
        strategy.submit_order(order, position_id=state.position_id if role == "add" else None)

    def _refresh_protection(self, state: DirectionState) -> None:
        owner = self._owner()
        if owner.instrument is None or not state.invested or state.position_id is None:
            return
        if state.emergency_exit_reason is not None:
            self._cancel_state_orders(state)
            self._submit_emergency_exit(state, state.emergency_exit_reason)
            return
        if owner.config.time_gated_stop_trial and state.phase != "remainder":
            current_stop = self._cached_order(state.stop_order_id)
            if current_stop is not None and not current_stop.is_pending_cancel:
                self._strategy().cancel_order(current_stop)
        else:
            stop_price = self._hard_stop_price(state)
            if stop_price <= 0:
                self._cancel_state_orders(state)
                self._submit_emergency_exit(state, "invalid_stop_inputs")
                return
            market = self._buy_market_price()
            if market is not None and market >= owner.instrument.make_price(stop_price).as_decimal():
                meta = self._order_meta.get(state.stop_order_id)
                requires_wait = (state.phase != "remainder" if owner._stop_target_price(state) is not None
                                 else meta.stop_requires_reentry_wait if meta is not None else False)
                reason = "fixed_stop_triggered" if requires_wait else "remainder_protection_triggered"
                state.exit_started = True
                state.emergency_requires_reentry_wait = requires_wait
                state.emergency_exit_reason = reason
                self._cancel_state_orders(state)
                self._submit_emergency_exit(state, reason)
                return
            self._upsert_exit(state, "stop", state.quantity, stop_price)
        if not state.invested or state.emergency_exit_reason is not None:
            return  # Submitting/modifying can synchronously fill or trigger cleanup.
        prices = owner._take_profit_prices(state)
        if prices is None:
            return
        first_price, second_price = prices
        second = self._cached_order(state.second_take_profit_order_id)
        second_meta = self._order_meta.get(state.second_take_profit_order_id)
        second_is_full = (state.phase == "scaled" and (
            state.second_take_profit_full_exit
            or owner.instrument.make_price(second_price) >= owner.instrument.make_price(first_price)
        ))
        if state.phase == "initial":
            first_quantity, second_quantity = state.quantity, Decimal(0)
        elif state.phase == "remainder":
            first_quantity, second_quantity = Decimal(0), state.quantity
        elif second_is_full:
            first_quantity, second_quantity = Decimal(0), state.quantity
            # 全平分支按P1定价；保留同一订单以连续维护部分成交后的剩余数量。
            second_price = first_price
        elif second is not None and not (second_meta is not None and second_meta.full_exit):
            # 加仓后恢复两份：沿用原余仓止盈单，补足部分在下轨止盈。
            second_quantity = min(state.quantity, second.quantity.as_decimal() - second.filled_qty.as_decimal())
            first_quantity = max(Decimal(0), state.quantity - second_quantity)
        else:
            second_quantity = min(state.quantity, owner._floor_quantity(state.unit_quantity / 2))
            first_quantity = state.quantity - second_quantity
        full_exit = (second_is_full or state.exit_started
                     or state.phase != "scaled" or second_quantity == 0)
        # Remove the obsolete leg before expanding the surviving limit order.
        for order_id, quantity in ((state.take_profit_order_id, first_quantity),
                                   (state.second_take_profit_order_id, second_quantity)):
            if quantity == 0:
                obsolete = self._cached_order(order_id)
                if obsolete is not None and not obsolete.is_pending_cancel:
                    self._strategy().cancel_order(obsolete)
        for role, quantity, price in (("take_profit_second", second_quantity, second_price),
                                      ("take_profit", first_quantity, first_price)):
            if not state.invested or state.emergency_exit_reason is not None:
                return
            if quantity > 0:
                self._upsert_exit(state, role, quantity, price, full_exit=full_exit)

    def _hard_stop_price(self, state: DirectionState) -> Decimal:
        target = self._owner()._stop_target_price(state)
        if target is not None:
            return target
        current = self._cached_order(state.stop_order_id)
        return current.trigger_price.as_decimal() if current is not None else Decimal(0)

    def _upsert_exit(
        self,
        state: DirectionState,
        role: str,
        quantity: Decimal,
        price: Decimal,
        full_exit: bool = False,
    ) -> None:
        owner = self._owner()
        strategy = self._strategy()
        if owner.instrument is None or quantity <= 0:
            return
        attribute = {"take_profit": "take_profit_order_id",
                     "take_profit_second": "second_take_profit_order_id", "stop": "stop_order_id"}[role]
        order_id = getattr(state, attribute)
        current = self._cached_order(order_id)
        quantity_obj = owner.instrument.make_qty(quantity)
        price_obj = owner.instrument.make_price(price)
        if current is not None:
            total_quantity = owner.instrument.make_qty(current.filled_qty.as_decimal() + quantity)
            meta = self._order_meta.get(current.client_order_id)
            if meta is not None:
                meta.full_exit = full_exit
                if role == "stop" and owner._stop_target_price(state) is not None:
                    meta.stop_requires_reentry_wait = state.phase != "remainder"
                meta.quantity = total_quantity.as_decimal()
            if role in ("take_profit", "take_profit_second"):
                if current.quantity != total_quantity or current.price != price_obj:
                    strategy.modify_order(current, quantity=total_quantity, price=price_obj)
            elif current.quantity != total_quantity or current.trigger_price != price_obj:
                strategy.modify_order(current, quantity=total_quantity, trigger_price=price_obj)
            return
        side = OrderSide.BUY
        if role in ("take_profit", "take_profit_second"):
            order = strategy.order_factory.limit(
                instrument_id=owner.config.instrument_id,
                order_side=side,
                quantity=quantity_obj,
                price=price_obj,
                time_in_force=TimeInForce.GTC,
                reduce_only=True,
                tags=[f"ethusdt-mean-reversion-short:take-profit:{state.direction}:{role}"],
            )
        else:
            order = strategy.order_factory.stop_market(
                instrument_id=owner.config.instrument_id,
                order_side=side,
                quantity=quantity_obj,
                trigger_price=price_obj,
                time_in_force=TimeInForce.GTC,
                reduce_only=True,
                tags=[f"ethusdt-mean-reversion-short:hard-stop:{state.direction}"],
            )
        setattr(state, attribute, order.client_order_id)
        self._order_meta[order.client_order_id] = OrderMeta(
            direction=state.direction,
            role=role,
            quantity=quantity,
            full_exit=full_exit,
            stop_requires_reentry_wait=role == "stop" and state.phase != "remainder",
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
        self._observe_market_price(price, event.ts_event)
        order = strategy.cache.order(event.client_order_id)
        if order is not None and order.is_closed:
            self._order_meta.pop(event.client_order_id, None)

        if meta.role in ("initial", "add"):
            was_flat = not state.invested
            state.apply_entry(quantity, price, commission)
            owner._snapshot_add_distance(state)
            state.position_id = event.position_id
            if meta.role == "initial" and was_flat:
                state.unit_quantity = meta.quantity
                state.position_start_ns = event.ts_event
                state.cycle_risk_budget = meta.planned_risk
                state.last_initial_fill_ns = event.ts_event
                state.last_initial_fill_price = price
                owner._lock_entry_ma99(state)
            if meta.role == "add":
                state.last_add_fill_ns = event.ts_event
                if state.phase != "scaled":
                    state.second_take_profit_full_exit = False
                state.phase = "scaled"
            if meta.invalidated:
                state.exit_started = True
                self._cancel_state_orders(state)
                self._submit_emergency_exit(state, "late_entry_after_exit")
                return
            if order is not None and order.is_closed:
                state.entry_order_id = None
            self._refresh_protection(state)
            if state.invested and not state.exit_started:
                owner._refresh_add_order(state, event.ts_event)
            return

        is_take_profit = meta.role in ("take_profit", "take_profit_second")
        if meta.role == "take_profit_second" and meta.full_exit and state.phase == "scaled":
            state.second_take_profit_full_exit = True
        state.exit_started = state.exit_started or not is_take_profit or meta.full_exit
        if meta.role in ("stop", "stop_emergency") and meta.stop_requires_reentry_wait:
            state.stop_exit_started = True
        state.apply_exit(quantity)
        owner._snapshot_add_distance(state)
        if (meta.role == "take_profit" and state.phase == "scaled" and state.invested
                and state.quantity <= state.unit_quantity / 2 and (order is None or order.is_closed)):
            state.phase = "remainder"
        if meta.role == "take_profit":
            state.take_profit_order_id = None if order is None or order.is_closed else event.client_order_id
        elif meta.role == "take_profit_second":
            state.second_take_profit_order_id = None if order is None or order.is_closed else event.client_order_id
        elif meta.role == "stop":
            state.stop_order_id = None if order is None or order.is_closed else event.client_order_id
        # 跳空同时越过两张止盈时，先完成全部退出，不在中间补仓。
        if is_take_profit:
            for order_id in (state.take_profit_order_id, state.second_take_profit_order_id):
                remaining = self._cached_order(order_id)
                if (order_id != event.client_order_id and remaining is not None
                        and remaining.price.as_decimal() >= price):
                    state.exit_started = True
        entry_meta = self._order_meta.get(state.entry_order_id)
        if entry_meta is not None and (state.exit_started or not state.invested):
            entry_meta.invalidated = True
        self._cancel_entry(state)

        if state.quantity <= owner._size_increment() / 2:
            if state.stop_exit_started:
                owner.recovery_model.reset_after_hard_stop(state, event.ts_event)
            self._cancel_state_orders(state, exclude=event.client_order_id)
            state.clear_cycle()
        else:
            self._refresh_protection(state)
            if is_take_profit and not state.exit_started:
                owner._refresh_add_order(state, event.ts_event)

    def on_order_canceled(self, event: OrderCanceled) -> None:
        self._handle_closed_order(event.client_order_id)

    def on_order_expired(self, event: OrderExpired) -> None:
        self._handle_closed_order(event.client_order_id)

    def on_order_denied(self, event: OrderDenied) -> None:
        self._handle_failed_order(event.client_order_id)

    def on_order_rejected(self, event: OrderRejected) -> None:
        self._handle_failed_order(event.client_order_id)

    def on_order_updated(self, event: OrderUpdated) -> None:
        # 回测撮合器可能把独立 reduce-only 单同步为全仓数量；保持每张单的目标数量。
        meta = self._order_meta.get(event.client_order_id)
        if meta is None or meta.role not in ("take_profit", "take_profit_second"):
            return
        order = self._cached_order(event.client_order_id)
        owner = self._owner()
        if order is None or order.is_pending_cancel or owner.instrument is None:
            return
        target = max(order.filled_qty.as_decimal(), meta.quantity)
        if order.quantity.as_decimal() != target:
            self._strategy().modify_order(order, quantity=owner.instrument.make_qty(target))

    def on_order_modify_rejected(self, event: OrderModifyRejected) -> None:
        meta = self._order_meta.get(event.client_order_id)
        if meta is not None and meta.role == "stop":
            state = self._owner().trade_state
            self._strategy().log.error(
                f"保护性止损单修改失败，提交应急平仓：{event.client_order_id}；原因：{event.reason}",
            )
            state.emergency_requires_reentry_wait = meta.stop_requires_reentry_wait
            self._submit_emergency_exit(state, "stop_modify_failed")

    def _handle_failed_order(self, order_id: ClientOrderId) -> None:
        meta = self._order_meta.get(order_id)
        self._handle_closed_order(order_id)
        if meta is not None and meta.role == "stop":
            state = self._owner().trade_state
            self._strategy().log.error(f"保护性止损单失败，提交应急平仓：{order_id}")
            state.emergency_requires_reentry_wait = meta.stop_requires_reentry_wait
            self._submit_emergency_exit(state, "stop_order_failed")

    def _handle_closed_order(self, order_id: ClientOrderId) -> None:
        meta = self._order_meta.pop(order_id, None)
        if meta is None:
            return
        state = self._owner().trade_state
        if state.entry_order_id == order_id:
            state.entry_order_id = None
        if state.take_profit_order_id == order_id:
            state.take_profit_order_id = None
        if state.second_take_profit_order_id == order_id:
            state.second_take_profit_order_id = None
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
            state.second_take_profit_order_id,
            state.stop_order_id,
        ):
            if order_id is None or order_id == exclude:
                continue
            meta = self._order_meta.get(order_id)
            if meta is not None and meta.role in ("initial", "add"):
                meta.invalidated = True
            order = self._cached_order(order_id)
            if order is not None and not order.is_pending_cancel:
                self._strategy().cancel_order(order)

    def _submit_emergency_exit(self, state: DirectionState, reason: str) -> None:
        owner = self._owner()
        strategy = self._strategy()
        if owner.instrument is None or not state.invested or state.position_id is None:
            return
        if any(
            meta.direction == state.direction and meta.role in (
                "stop_emergency", "cycle_cleanup"
            )
            for meta in self._order_meta.values()
        ):
            return
        state.emergency_exit_reason = reason
        if reason == "certain_same_bar_stop":
            state.emergency_requires_reentry_wait = state.phase != "remainder"
        side = OrderSide.BUY
        normal_trigger = reason in ("fixed_stop_triggered", "remainder_protection_triggered")
        tag = (f"ethusdt-mean-reversion-short:triggered-exit:{reason}" if normal_trigger
               else f"ethusdt-mean-reversion-short:emergency:{reason}")
        order = strategy.order_factory.market(
            instrument_id=owner.config.instrument_id,
            order_side=side,
            quantity=owner.instrument.make_qty(state.quantity),
            time_in_force=TimeInForce.IOC,
            reduce_only=True,
            tags=[tag],
        )
        self._order_meta[order.client_order_id] = OrderMeta(
            direction=state.direction,
            role="cycle_cleanup" if reason == "late_entry_after_exit" else "stop_emergency",
            quantity=state.quantity,
            stop_requires_reentry_wait=state.emergency_requires_reentry_wait,
        )
        strategy.submit_order(order, position_id=state.position_id)

    def _certain_initial_fill_then_stop(self, state: DirectionState, bar: Bar) -> bool:
        if self._owner().config.time_gated_stop_trial and state.phase != "remainder":
            return False
        if (
            not state.invested
            or state.last_initial_fill_ns != bar.ts_event
            or state.last_initial_fill_price is None
        ):
            return False
        entry = state.last_initial_fill_price
        stop = self._hard_stop_price(state)
        return bar.open.as_decimal() <= entry and bar.high.as_decimal() >= stop
