"""Production-candidate factories and confirmed-order snapshot lifecycle."""

from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest

from strategy_python._mechanism.factory import STRATEGIES, create_strategy
from strategy_python._mechanism.rules import mean_reversion_snapshot
from strategy_python.mechanism_mean_reversion_long_5min.execution import (
    EthExecutionMixin,
    OrderMeta,
)
from strategy_python.mechanism_mean_reversion_long_5min.state import DirectionState


@pytest.mark.parametrize("asset", ["ETH", "BTC"])
@pytest.mark.parametrize("key", STRATEGIES)
def test_candidate_factory_uses_correct_market_and_rejects_training_trades(asset, key):
    candidate = create_strategy(asset, key)
    assert str(candidate.config.instrument_id) == f"{asset}USDT-PERP.BINANCE"
    assert candidate.config.trade_start_ns == 1704067200000000000
    with pytest.raises(ValueError, match="2024"):
        create_strategy(asset, key, datetime(2023, 12, 31, tzinfo=UTC))
    if key == "PBS":
        assert candidate.config.risk_fraction == 0.015
        assert candidate.config.max_notional_fraction == 3


class Number:
    def __init__(self, value):
        self.value = Decimal(value)

    def as_decimal(self):
        return self.value

    def __eq__(self, other):
        return isinstance(other, Number) and self.value == other.value


class Harness(EthExecutionMixin):
    def __init__(self):
        self._initialize_execution()
        self.trade_state = DirectionState(entry_order_id="entry")
        self.instrument = SimpleNamespace(make_qty=Number, make_price=Number, price_precision=2)
        self.order = SimpleNamespace(
            client_order_id="entry", quantity=Number("1"), price=Number("100"), is_closed=False
        )
        self.cache = SimpleNamespace(order=lambda _: self.order)
        self._six_hour_history = []
        self.modifications = []
        self.protected_snapshots = []

    def modify_order(self, order, **changes):
        self.modifications.append(changes)

    def _refresh_protection(self, state):
        self.protected_snapshots.append(state.cycle_snapshot)


def test_mrl_rejected_change_keeps_confirmed_snapshot_and_fill_uses_it():
    owner = Harness()
    old = mean_reversion_snapshot(100, 101, (0.3, 0.9, 2.3))
    new = mean_reversion_snapshot(100, 102, (0.3, 0.9, 2.3))
    owner._order_meta["entry"] = OrderMeta(1, "initial", Decimal(1), snapshot=old)
    owner._upsert_entry(owner.trade_state, "initial", Decimal(2), Decimal(99), snapshot=new)
    assert owner._order_meta["entry"].snapshot is old
    assert owner._pending_entry_meta["entry"].snapshot is new
    owner.on_order_modify_rejected(SimpleNamespace(client_order_id="entry"))
    assert "entry" not in owner._pending_entry_meta
    assert owner._order_meta["entry"].snapshot is old
    owner.order.is_closed = True
    owner.on_order_filled(
        SimpleNamespace(
            client_order_id="entry",
            last_qty=Number(1),
            last_px=Number(100),
            commission=Number(".02"),
            position_id="position",
            ts_event=1,
        )
    )
    assert owner.trade_state.cycle_snapshot is old
    assert owner.protected_snapshots == [old]


def test_mrl_confirmed_change_promotes_snapshot_and_late_update_cannot_resurrect_order():
    owner = Harness()
    old = mean_reversion_snapshot(100, 101, (0.3, 0.9, 2.3))
    new = mean_reversion_snapshot(100, 102, (0.3, 0.9, 2.3))
    owner._order_meta["entry"] = OrderMeta(1, "initial", Decimal(1), snapshot=old)
    owner._upsert_entry(owner.trade_state, "initial", Decimal(2), Decimal(99), snapshot=new)
    owner.on_order_updated(SimpleNamespace(client_order_id="entry"))
    assert owner._order_meta["entry"].snapshot is new
    owner._handle_closed_order("entry")
    owner.on_order_updated(SimpleNamespace(client_order_id="entry"))
    assert "entry" not in owner._order_meta


def test_mrl_fill_before_update_confirmation_uses_old_snapshot():
    owner = Harness()
    old = mean_reversion_snapshot(100, 101, (0.3, 0.9, 2.3))
    new = mean_reversion_snapshot(100, 102, (0.3, 0.9, 2.3))
    owner._order_meta["entry"] = OrderMeta(1, "initial", Decimal(1), snapshot=old)
    owner._upsert_entry(owner.trade_state, "initial", Decimal(2), Decimal(99), snapshot=new)
    owner.order.is_closed = True
    owner.on_order_filled(
        SimpleNamespace(
            client_order_id="entry",
            last_qty=Number(1),
            last_px=Number(100),
            commission=Number(".02"),
            position_id="position",
            ts_event=1,
        )
    )
    owner.on_order_updated(SimpleNamespace(client_order_id="entry"))
    assert owner.trade_state.cycle_snapshot is old
    assert "entry" not in owner._order_meta


def test_late_add_after_closed_cycle_is_flattened_not_treated_as_new_signal():
    owner = Harness()
    snapshot = mean_reversion_snapshot(100, 101, (0.3, 0.9, 2.3))
    owner._order_meta["entry"] = OrderMeta(1, "add", Decimal(1), snapshot=snapshot)
    owner.order.is_closed = True
    cancelled, exits = [], []
    owner._cancel_state_orders = lambda state, **kwargs: cancelled.append(state)
    owner._submit_emergency_exit = lambda state, reason, **kwargs: exits.append(
        (state.quantity, reason, kwargs)
    )
    owner.on_order_filled(
        SimpleNamespace(
            client_order_id="entry",
            last_qty=Number(1),
            last_px=Number(100),
            commission=Number(".02"),
            position_id="late_position",
            ts_event=1,
        )
    )
    assert owner.protected_snapshots == []
    assert cancelled == [owner.trade_state]
    assert exits == [(Decimal(1), "late_add_after_flat", {"role": "orphan_exit"})]
    assert owner.trade_state.cycle_risk_budget == 0
