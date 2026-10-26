"""Create isolated mechanism candidates; never edit frozen original packages."""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parents[2]
SOURCES = {
    "MRL_original": ("mean_reversion_long_5min", "MeanReversionLong5Min", 135),
    "MRL_optimized": ("mean_reversion_long_optimized_5min", "MeanReversionLongOptimized5Min", 135),
    "TFL": ("trend_following_long_5min", "TrendFollowingLong5Min", 11),
    "TFS": ("trend_following_short_5min", "TrendFollowingShort5Min", 21),
    "PBS": ("pullback_short_1h", "PullbackShort1h", 30),
}


def swap(code, old, new):
    if old not in code:
        raise ValueError(f"Missing replacement: {old[:90]}")
    return code.replace(old, new)


def main():
    registry = {}
    for key, (source, prefix, warmup) in SOURCES.items():
        package, new_prefix = "mechanism_" + source, "Mechanism" + prefix
        target = PROJECT / "strategy_python" / package
        target.mkdir(exist_ok=True)
        for path in (PROJECT / "strategy_python" / source).glob("*.py"):
            if path.name == "backtest.py":
                continue
            code = path.read_text().replace(source, package).replace(prefix, new_prefix)
            if path.name == "config.py":
                if key.startswith("MRL"):
                    extra = '\n    # Conditional pre-recovery quantiles, in entry BOLL-gap units.\n    mechanism_add_multipliers: tuple[float, float, float] = (0.3, 0.9, 2.3)\n    mechanism_flat_risk: bool = False\n    mechanism_flat_multiplier: float = 1.0\n'
                elif key in ("TFL", "TFS"):
                    extra = '\n    mechanism_stop_atr: float = 1.0\n    mechanism_activation_atr: float = 2.0\n    mechanism_price_risk: float = 0.005\n'
                else:
                    extra = '\n    mechanism_buffer_atr: float = 0.0\n    mechanism_min_stop_atr: float = 1.0\n    mechanism_max_stop_atr: float = 3.0\n'
                code += extra
            if path.name == "strategy.py":
                # Guard applies to actual candidate trading, not preheat bars.
                code = swap(code, "        super().__init__(config)", '        if config.trade_start_ns < 1704067200000000000:\n            raise ValueError("Mechanism calibration ends 2024-01-01; earlier trading is invalid")\n        super().__init__(config)')
                code = swap(code, "from nautilus_trader.trading.strategy import Strategy", "from nautilus_trader.trading.strategy import Strategy\nfrom strategy_python._mechanism.rules import HourlyATR, trend_snapshot, mean_reversion_snapshot, pullback_stop")
                if key in ("TFL", "TFS"):
                    code = swap(code, "        self._initialize_legs()", "        self._initialize_legs()\n        self.mechanism_atr = HourlyATR()\n        self.mechanism_snapshot = None")
                    code = swap(code, "            self.hourly_trend.update(hourly.close)", "            self.hourly_trend.update(hourly.close)\n            self.mechanism_atr.update(bar.ts_event + 1_000_000, hourly.high, hourly.low, hourly.close)")
                    code = code.replace("self.config.take_profit_activation_return", "self.mechanism_snapshot.activation_return")
                    code = code.replace("self.config.max_loss_return", "self.mechanism_snapshot.stop_return")
                    code = swap(code, "        quantity = equity_quantity(", '''        snapshot = trend_snapshot(
            float(close), self.mechanism_atr.value,
            self.config.mechanism_stop_atr, self.config.mechanism_activation_atr,
            self.config.mechanism_price_risk, self.config.equity_fraction_per_side,
        )
        if snapshot is None:
            return
        self.mechanism_snapshot = snapshot
        quantity = equity_quantity(''')
                    code = swap(code, "Decimal(str(self.config.equity_fraction_per_side))", "Decimal(str(snapshot.notional_fraction))")
                    code = swap(code, "        self._validate_config(config)", '''        self._validate_config(config)
        if config.max_positions_per_side != 1 or config.entry_trigger != "ma_cross" or not config.peak_profit_exit:
            raise ValueError("Mechanism trend supports one market-cross/peak-exit leg")
        if min(config.mechanism_stop_atr, config.mechanism_activation_atr, config.mechanism_price_risk) <= 0:
            raise ValueError("Mechanism coefficients must be positive")''')
                elif key.startswith("MRL"):
                    code = swap(code, "from decimal import", "from dataclasses import replace\nfrom decimal import")
                    code = swap(code, "        recovery = self.recovery_model.risk_coefficient(state, now_ns)", '''        lower, middle, _ = self.features.bollinger
        snapshot = mean_reversion_snapshot(lower, middle, self.config.mechanism_add_multipliers,
                                           2 * self.config.entry_fee_rate)
        if snapshot is None:
            self._cancel_entry(state)
            return
        self.risk_model.parameters = replace(self.risk_model.parameters, add_offsets=snapshot.add_offsets)
        recovery = self.recovery_model.risk_coefficient(state, now_ns)''')
                    code = swap(code, '                role="initial",', '                role="initial",\n                snapshot=snapshot,')
                    code = swap(code, "        offset = dict(self.risk_model.parameters.add_offsets)[next_level]", '''        if state.cycle_snapshot is None:
            raise RuntimeError("Missing confirmed entry snapshot")
        self.risk_model.parameters = replace(self.risk_model.parameters, add_offsets=state.cycle_snapshot.add_offsets)
        offset = dict(state.cycle_snapshot.add_offsets)[next_level]''')
                    code = swap(code, "        self.risk_model = DirectionalRiskModel(parameters)", '''        self.risk_model = DirectionalRiskModel(parameters)
        if config.mechanism_flat_risk:
            self.risk_model.parameters = replace(parameters, risk_fraction=parameters.risk_fraction * config.mechanism_flat_multiplier)
            self.risk_model.strength_coefficient = lambda strength: 1.0
            self.risk_model.boundary_coefficient = lambda direction, position: 1.0''')
                else:
                    code = swap(code, "        stop = Decimal(str(features.swing_high + self.config.stop_atr_buffer * features.atr))", '''        stop_value = pullback_stop(
            float(self.last_price), features.swing_high, features.atr,
            float(self.instrument.price_increment), self.config.mechanism_buffer_atr,
            self.config.mechanism_min_stop_atr, self.config.mechanism_max_stop_atr,
        )
        if stop_value is None:
            return
        stop = Decimal(str(stop_value))''')
                    code = swap(code, '        distance = float(stop / self.last_price - 1)\n        if not self.config.min_stop_fraction <= distance <= self.config.max_stop_fraction:\n            return\n', '')
            if path.name == "execution.py" and key in ("TFL", "TFS"):
                code = code.replace("owner.config.max_loss_return", "owner.mechanism_snapshot.stop_return")
                code = swap(code, "            self._stopping\n", "            self._stopping\n            or owner.mechanism_snapshot is None\n")
            if key.startswith("MRL"):
                if path.name in ("state.py", "position.py", "execution.py"):
                    code = swap(code, "from decimal import", "from strategy_python._mechanism.rules import MeanReversionSnapshot\nfrom decimal import")
                if path.name == "state.py":
                    code = swap(code, "    quantity: Decimal =", "    cycle_snapshot: MeanReversionSnapshot | None = None\n    quantity: Decimal =")
                    code = swap(code, "        self.quantity = Decimal(0)", "        self.cycle_snapshot = None\n        self.quantity = Decimal(0)")
                if path.name == "position.py":
                    code += "\n    snapshot: MeanReversionSnapshot | None = None\n"
                if path.name == "execution.py":
                    code = swap(code, "    OrderModifyRejected,", "    OrderModifyRejected,\n    OrderUpdated,")
                    code = code.replace("    risk_context: dict[str, float] | None = None", "    risk_context: dict[str, float] | None = None\n    snapshot: MeanReversionSnapshot | None = None", 1)
                    code = swap(code, "        self._order_meta: dict[ClientOrderId, OrderMeta] = {}", "        self._order_meta: dict[ClientOrderId, OrderMeta] = {}\n        self._pending_entry_meta: dict[ClientOrderId, OrderMeta] = {}")
                    code = swap(code, "        self._order_meta.clear()", "        self._order_meta.clear()\n        self._pending_entry_meta.clear()")
                    code = swap(code, "            risk_context=target.risk_context,", "            risk_context=target.risk_context,\n            snapshot=target.snapshot,")
                    code = swap(code, "        risk_context: dict[str, float] | None = None,", "        risk_context: dict[str, float] | None = None,\n        snapshot: MeanReversionSnapshot | None = None,")
                    code = swap(code, "            risk_context=risk_context,", "            risk_context=risk_context,\n            snapshot=snapshot,")
                    code = swap(code, '''            self._order_meta[current.client_order_id] = new_meta
            if current.quantity != quantity_obj or current.price != price_obj:
                strategy.modify_order(current, quantity=quantity_obj, price=price_obj)
            return''', '''            if current.client_order_id in self._pending_entry_meta:
                return
            if current.quantity != quantity_obj or current.price != price_obj:
                self._pending_entry_meta[current.client_order_id] = new_meta
                strategy.modify_order(current, quantity=quantity_obj, price=price_obj)
            else:
                self._order_meta[current.client_order_id] = new_meta
            return''')
                    code = swap(code, "owner.config.take_profit_rate", "state.cycle_snapshot.take_profit_return")
                    code = swap(code, '            if meta.role == "initial" and was_flat:', '''            if meta.role == "initial" and was_flat:
                if meta.snapshot is None:
                    raise RuntimeError("Initial fill lacks a confirmed scale snapshot")
                state.cycle_snapshot = meta.snapshot''')
                    code = swap(code, "            self._order_meta.pop(event.client_order_id, None)", "            self._order_meta.pop(event.client_order_id, None)\n            self._pending_entry_meta.pop(event.client_order_id, None)")
                    code = swap(code, "    def on_order_modify_rejected(self, event: OrderModifyRejected) -> None:", '''    def on_order_updated(self, event: OrderUpdated) -> None:
        pending = self._pending_entry_meta.pop(event.client_order_id, None)
        if pending is not None and event.client_order_id in self._order_meta:
            self._order_meta[event.client_order_id] = pending

    def on_order_modify_rejected(self, event: OrderModifyRejected) -> None:
        self._pending_entry_meta.pop(event.client_order_id, None)''')
                    code = swap(code, "    def _handle_closed_order(self, order_id: ClientOrderId) -> None:", "    def _handle_closed_order(self, order_id: ClientOrderId) -> None:\n        self._pending_entry_meta.pop(order_id, None)")
            if key.startswith("MRL") and path.name == "strategy.py":
                code = swap(code, '                role="add",', '                role="add",\n                snapshot=state.cycle_snapshot,')
            if key.startswith("MRL") and path.name == "execution.py":
                code = swap(code, "for order_id, meta in self._order_meta.items():", "for order_id, meta in {**self._order_meta, **self._pending_entry_meta}.items():")
                code = swap(code, "            state.position_id = event.position_id\n", '''            state.position_id = event.position_id
            if meta.role == "add" and was_flat:
                state.cycle_snapshot = meta.snapshot
                state.unit_quantity = meta.quantity
                state.position_start_ns = event.ts_event
                self._cancel_state_orders(state, exclude=event.client_order_id)
                self._submit_emergency_exit(state, "late_add_after_flat", role="orphan_exit")
                return
''')
                code = swap(code, "def _submit_emergency_exit(self, state: DirectionState, reason: str) -> None:", 'def _submit_emergency_exit(self, state: DirectionState, reason: str, *, role: str = "stop_emergency") -> None:')
                code = swap(code, 'meta.direction == state.direction and meta.role == "stop_emergency"', 'meta.direction == state.direction and meta.role in ("stop_emergency", "orphan_exit")')
                code = swap(code, '            role="stop_emergency",', '            role=role,')
            (target / path.name).write_text(code)
        registry[key] = {"package": package, "prefix": new_prefix, "source": source, "warmup_days": warmup}
    (PROJECT / "strategy_python/_mechanism/strategies.json").write_text(json.dumps(registry, indent=2) + "\n")
    print("BUILT", list(registry))


if __name__ == "__main__":
    main()
