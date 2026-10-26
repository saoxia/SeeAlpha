from decimal import Decimal

import pytest

from nautilus_support import TestInstrumentProvider
from strategy_python.mean_reversion_short_5min import MeanReversionShort5MinStrategy
from strategy_python.mean_reversion_short_5min.position import add_entry_price
from tests.strategy_python.mean_reversion_short_5min.strategy_test import make_config


@pytest.mark.parametrize('upper,ma,distance,expected', [
    (110., 120., Decimal(30), '110'),
    (100.5, 120., Decimal(30), '100.5'),
    (100., 99., Decimal(10), '109'),
    (100., 95., Decimal(-20), '100.5'),
    (110., None, None, '110'),
    (100., 95., None, None),
])
def test_add_price_branches_and_inclusive_threshold(upper, ma, distance, expected):
    price = add_entry_price(Decimal(100), (90., 95., upper), ma, distance)
    assert price == (Decimal(expected) if expected is not None else None)


def test_add_locks_distance_but_tracks_current_ma_and_boll():
    targets = []

    class Observe(MeanReversionShort5MinStrategy):
        def apply_entry_target(self, state, target):
            targets.append(target)

    strategy = Observe(make_config())
    strategy.instrument = TestInstrumentProvider.ethusdt_perp_binance()
    state = strategy.trade_state
    state.quantity = state.unit_quantity = Decimal(1)
    state.average_price = Decimal(100)
    state.entry_ma99 = Decimal(95)
    state.cycle_risk_budget = Decimal(200)
    f = strategy.features
    f.stop_ma_distance.current_ma, f.stop_p995 = 160., 5.
    f.bollinger = (90., 95., 100.)
    f.ma_distance.current_ma = 95.
    f.add_distance_mean = 7.
    strategy._snapshot_add_distance(state)
    strategy._refresh_add_order(state, 1)
    assert targets[-1].price == Decimal(102)
    f.add_distance_mean = 30.
    strategy._refresh_add_order(state, 2)
    assert targets[-1].price == Decimal(102)
    f.ma_distance.current_ma = 97.
    strategy._refresh_add_order(state, 3)
    assert targets[-1].price == Decimal(104)
    f.bollinger = (90., 100., 110.)
    strategy._refresh_add_order(state, 4)
    assert targets[-1].price == Decimal(110)
    state.clear_cycle()
    assert state.add_distance_snapshot is None
