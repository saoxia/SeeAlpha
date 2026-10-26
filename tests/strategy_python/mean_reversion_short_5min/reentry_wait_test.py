from decimal import Decimal
from types import SimpleNamespace

import pytest
from nautilus_trader.model import Money

from nautilus_support import TestInstrumentProvider
from strategy_python.mean_reversion_short_5min import MeanReversionShort5MinStrategy
from strategy_python.mean_reversion_short_5min.execution import OrderMeta
from strategy_python.mean_reversion_short_5min.risk import RecoveryModel
from strategy_python.mean_reversion_short_5min.state import DirectionState
from tests.strategy_python.mean_reversion_short_5min.strategy_test import completed_bar, make_config

STEP = 300_000_000_000


def bar(index, high=99, above=False):
    return completed_bar(index * STEP, high=105 if above else high,
                         low=101 if above else 90, close=103 if above else 95, minutes=5)


def test_initial_entry_waits_for_all_three_stages_after_flat():
    targets = []

    class Observe(MeanReversionShort5MinStrategy):
        def apply_entry_target(self, state, target):
            targets.append(target)

        def _cancel_entry(self, state):
            pass

        def _account_equity(self):
            return Decimal(500)

    strategy = Observe(make_config())
    strategy.instrument = TestInstrumentProvider.ethusdt_perp_binance()
    f = strategy.features
    f.ma_distance.current_ma = 100.
    f.entry_distance_allowed = lambda price: True
    f.entry_touch_allowed = lambda now: True
    f.add_distance_mean, f.stop_p995 = 10., 5.
    f.stop_ma_distance.current_ma = 140.
    f.bollinger = (90., 95., 100.)
    f.on_five_minute_bar = lambda bar: f.bollinger
    state = strategy.trade_state
    strategy.recovery_model.reset_after_hard_stop(state, STEP // 2)
    state.clear_cycle()
    strategy._on_five_minute_bar(bar(1))
    assert state.reentry_stage_count == 0
    for i in range(2, 17):
        strategy._on_five_minute_bar(bar(i, above=7 <= i <= 11))
        if i < 16:
            assert state.reentry_waiting and not targets
    assert not state.reentry_waiting and len(targets) == 1
    strategy.recovery_model.reset_after_hard_stop(state, 17 * STEP)
    assert state.reentry_stage == state.reentry_stage_count == 0


@pytest.mark.parametrize('stage', [0, 1, 2])
def test_touching_ma_resets_only_current_stage_count(stage):
    state = DirectionState()
    model = RecoveryModel()
    model.reset_after_hard_stop(state, 0)
    state.reentry_stage = stage
    for i in range(1, 5):
        model.on_five_minute_bar(state, bar(i, above=stage == 1), 100.)
    touch = completed_bar(5 * STEP, high=105 if stage == 1 else 100,
                          low=100 if stage == 1 else 90, close=100, minutes=5)
    model.on_five_minute_bar(state, touch, 100.)
    assert state.reentry_stage == stage and state.reentry_stage_count == 0
    for i in range(6, 11):
        result = model.on_five_minute_bar(state, bar(i, above=stage == 1), 100.)
    assert result is (stage == 2)
    assert state.reentry_waiting is (stage != 2)


@pytest.mark.parametrize('ma', [None, float('nan')])
def test_missing_ma_restarts_entire_recovery(ma):
    state = DirectionState()
    model = RecoveryModel()
    model.reset_after_hard_stop(state, 0)
    state.reentry_stage, state.reentry_stage_count = 2, 4
    model.on_five_minute_bar(state, bar(1), ma)
    assert state.reentry_stage == state.reentry_stage_count == 0
    assert state.reentry_waiting


def test_wrong_order_duplicate_and_gap_do_not_unlock():
    state = DirectionState()
    model = RecoveryModel()
    model.reset_after_hard_stop(state, 0)
    for i in range(1, 21):
        model.on_five_minute_bar(state, bar(i), 100.)
    assert state.reentry_waiting and state.reentry_stage == 1
    for i in range(21, 25):
        model.on_five_minute_bar(state, bar(i, above=True), 100.)
    model.on_five_minute_bar(state, bar(24, above=True), 100.)
    assert state.reentry_stage_count == 4
    model.on_five_minute_bar(state, bar(26), 100.)
    assert state.reentry_stage == 0 and state.reentry_stage_count == 1


@pytest.mark.parametrize('first_role,expected', [('stop', True), ('stop_emergency', True),
                                               ('stop', False), ('stop_emergency', False),
                                               ('take_profit', False)])
def test_exit_fills_lock_only_after_stop_cycle_is_fully_flat(first_role, expected):
    class Observe(MeanReversionShort5MinStrategy):
        def _strategy(self):
            return SimpleNamespace(cache=SimpleNamespace(
                order=lambda key: SimpleNamespace(is_closed=True)))

        def _cancel_entry(self, state):
            pass

        def _cancel_state_orders(self, state, exclude=None):
            pass

        def _refresh_protection(self, state):
            pass

    strategy = Observe(make_config())
    strategy.instrument = TestInstrumentProvider.ethusdt_perp_binance()
    state = strategy.trade_state
    state.quantity = state.unit_quantity = Decimal(1)
    state.average_price = Decimal(100)
    for key, role, qty, timestamp in [('first', first_role, .4, STEP),
                                      ('last', 'take_profit', .6, 2 * STEP)]:
        strategy._order_meta[key] = OrderMeta(-1, role, Decimal(str(qty)),
                                              stop_requires_reentry_wait=key == 'first' and expected)
        strategy.on_order_filled(SimpleNamespace(
            client_order_id=key, position_id='test', ts_event=timestamp,
            last_qty=strategy.instrument.make_qty(qty),
            last_px=strategy.instrument.make_price(110),
            commission=Money(0, strategy.instrument.quote_currency)))
        if key == 'first':
            assert not state.reentry_waiting
    assert not state.invested
    assert state.reentry_waiting is expected
    assert state.reentry_started_ns == (2 * STEP if expected else None)
    assert state.hard_stop_count == int(expected)
