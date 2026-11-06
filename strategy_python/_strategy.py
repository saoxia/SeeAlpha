"""Project lifecycle compatibility for NautilusTrader 2.0.0rc7."""
from functools import wraps

from nautilus_trader.trading import Strategy as NativeStrategy


class _CoordinatorCache:
    """成员读取订单时优先返回组合协调器收下、尚未发到交易所的虚拟订单。"""

    def __init__(self, native, coordinator) -> None:
        self._native = native
        self._coordinator = coordinator

    def order(self, client_order_id):
        virtual = self._coordinator.virtual_order(client_order_id)
        if virtual is not None:
            return virtual
        return self._native.order(client_order_id)

    def __getattr__(self, name):
        return getattr(self._native, name)


class Strategy(NativeStrategy):
    """Run explicit-stop cleanup before rc7 borrows its Rust strategy mutably."""

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        callback = cls.__dict__.get("on_stop")
        if callback is None:
            return

        @wraps(callback)
        def on_stop(self):
            if not getattr(self, "_native_stop_in_progress", False):
                return callback(self)
            return None

        cls.on_stop = on_stop

    def stop(self):
        if not self.is_running():
            return super().stop()
        self.on_stop()
        self._native_stop_in_progress = True
        try:
            return super().stop()
        finally:
            self._native_stop_in_progress = False

    def _active_portfolio_coordinator(self):
        coordinator = getattr(self, "_portfolio_coordinator", None)
        if coordinator is None or coordinator.trader_id is None:
            return None
        return coordinator

    def attach_portfolio_coordinator(self, coordinator) -> None:
        """启动前绑定净额协调器。未注册到引擎时成员仍按原路径自己下单。"""
        if getattr(self, "_portfolio_coordinator", None) is not None:
            raise ValueError("组合协调器只能绑定一次")
        if self.is_running():
            raise ValueError("组合协调器必须在启动前绑定")
        self._portfolio_coordinator = coordinator
        coordinator.register_member(self)

    @property
    def cache(self):
        native = super().cache
        coordinator = self._active_portfolio_coordinator()
        if coordinator is None:
            return native
        return _CoordinatorCache(native, coordinator)

    def submit_order(self, order, position_id=None, client_id=None, params=None):
        coordinator = self._active_portfolio_coordinator()
        if coordinator is not None:
            coordinator.accept_order(self, order, position_id)
            return None
        return super().submit_order(
            order, position_id=position_id, client_id=client_id, params=params,
        )

    def modify_order(
        self,
        client_order_id,
        quantity=None,
        price=None,
        trigger_price=None,
        client_id=None,
        params=None,
    ):
        coordinator = self._active_portfolio_coordinator()
        if coordinator is not None:
            coordinator.accept_modify(self, client_order_id, quantity, price, trigger_price)
            return None
        return super().modify_order(
            client_order_id,
            quantity=quantity,
            price=price,
            trigger_price=trigger_price,
            client_id=client_id,
            params=params,
        )

    def cancel_order(self, client_order_id, client_id=None, params=None):
        coordinator = self._active_portfolio_coordinator()
        if coordinator is not None:
            coordinator.accept_cancel(self, client_order_id)
            return None
        return super().cancel_order(client_order_id, client_id=client_id, params=params)

    def cancel_all_orders(
        self,
        instrument_id,
        order_side=None,
        client_id=None,
        strategy_only=True,
        params=None,
    ):
        coordinator = self._active_portfolio_coordinator()
        if coordinator is not None:
            coordinator.cancel_member_orders(self, instrument_id, order_side)
            return None
        return super().cancel_all_orders(
            instrument_id,
            order_side=order_side,
            client_id=client_id,
            strategy_only=strategy_only,
            params=params,
        )

    def close_position(
        self,
        position,
        client_id=None,
        tags=None,
        time_in_force=None,
        reduce_only=None,
        quote_quantity=None,
        params=None,
    ):
        coordinator = self._active_portfolio_coordinator()
        if coordinator is not None:
            coordinator.flatten_member(self, position.instrument_id)
            return None
        return super().close_position(
            position,
            client_id=client_id,
            tags=tags,
            time_in_force=time_in_force,
            reduce_only=reduce_only,
            quote_quantity=quote_quantity,
            params=params,
        )

    def close_all_positions(
        self,
        instrument_id,
        position_side=None,
        client_id=None,
        tags=None,
        time_in_force=None,
        reduce_only=None,
        quote_quantity=None,
        params=None,
    ):
        coordinator = self._active_portfolio_coordinator()
        if coordinator is not None:
            coordinator.flatten_member(self, instrument_id)
            return None
        return super().close_all_positions(
            instrument_id,
            position_side=position_side,
            client_id=client_id,
            tags=tags,
            time_in_force=time_in_force,
            reduce_only=reduce_only,
            quote_quantity=quote_quantity,
            params=params,
        )
