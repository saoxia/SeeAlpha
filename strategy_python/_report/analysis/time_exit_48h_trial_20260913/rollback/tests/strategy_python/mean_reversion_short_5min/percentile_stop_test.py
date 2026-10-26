from collections import deque
from decimal import Decimal

import pytest

from strategy_python.mean_reversion_short_5min import MeanReversionShort5MinStrategy
from strategy_python.mean_reversion_short_5min.features import EthFeatureModel
from strategy_python.mean_reversion_short_5min.indicators import RollingMaDistance
from tests.strategy_python.mean_reversion_short_5min.strategy_test import completed_bar, make_config

STEP = 300_000_000_000


def test_fourteen_day_window_requires_all_4032_distances_and_rolls():
    features = EthFeatureModel(make_config())
    for i in range(4129):
        features.on_five_minute_bar(completed_bar((i + 1) * STEP, close=1000 + i))
    assert features.stop_p995 is None
    features.on_five_minute_bar(completed_bar(4130 * STEP, close=5129))
    assert len(features.stop_ma_distance.samples) == 4032
    assert len(features.ma_distance.samples) == 576
    assert features.stop_ma_distance.current_ma == 5080
    assert features.stop_p995 == 49
    assert features.stop_p995 == 49.
    features.on_five_minute_bar(completed_bar(4131 * STEP, close=5130))
    assert len(features.stop_ma_distance.samples) == 4032
    assert features.stop_ma_distance.samples[0][0] == 100 * STEP
    assert features.stop_p995 == 49
    # Distinct tail values distinguish P99.5 (rank 4012) from the old P99.
    features.stop_ma_distance.samples = deque(
        ((100 + i) * STEP, float(i)) for i in range(4032)
    )
    features.on_five_minute_bar(completed_bar(4132 * STEP, close=5131))
    assert features.stop_p995 == 4011.
    features.reset()
    assert features.stop_p995 is None


def test_percentile_uses_signed_distances_and_same_empirical_rank_convention():
    indicator = RollingMaDistance(window_hours=336)
    indicator.samples = deque((i * STEP, float(-4032 + i)) for i in range(4032))
    assert indicator.quantile(.995) == -21
    assert indicator.quantile(.25) == -3024


@pytest.mark.parametrize('phase', ['initial', 'scaled'])
@pytest.mark.parametrize('average,expected', [('100', '130'), ('105', '136.5'), ('2000', '2600')])
def test_fixed_stop_tracks_average_without_ma_or_percentiles(phase, average, expected):
    strategy = MeanReversionShort5MinStrategy(make_config())
    state = strategy.trade_state
    state.phase = phase
    state.average_price = Decimal(average)
    assert state.entry_ma99 is None and strategy.features.stop_p995 is None
    assert strategy._hard_stop_price(state) == Decimal(expected)
    state.entry_ma99 = Decimal(9999)
    strategy.features.stop_ma_distance.current_ma = 9999.
    strategy.features.stop_p995 = -9999.
    assert strategy._hard_stop_price(state) == Decimal(expected)
    state.average_price = Decimal(220)
    assert strategy._hard_stop_price(state) == Decimal(286)


def test_fixed_stop_risk_sizing_and_add_budget_use_projected_average():
    strategy = MeanReversionShort5MinStrategy(make_config())
    sizing = strategy.risk_model.size_initial_entry(
        -1, 100, 0, Decimal(500), Decimal('.001'), second_entry_price=110)
    # Two units average 105, stop at 136.5: loss 63 plus fees and buffer.
    cost = (Decimal(63) + Decimal(210) * Decimal('.0002')
            + Decimal(273) * Decimal('.0005')) * Decimal('1.05')
    assert sizing['risk_per_quantity'] == cost
    assert sizing['planned_risk'] <= Decimal(150)
    state = strategy.trade_state
    state.quantity = state.unit_quantity = sizing['unit_quantity']
    state.average_price = Decimal(100)
    state.cycle_risk_budget = sizing['planned_risk']
    state.cycle_entry_fees = state.quantity * Decimal(100) * Decimal('.0002')
    assert strategy._entry_preserves_cycle_risk(state, state.quantity, Decimal(110))
    assert not strategy._entry_preserves_cycle_risk(state, state.quantity, Decimal(115))
    # An add above the projected stop remains invalid, even with ample budget.
    state.cycle_risk_budget = Decimal(10000)
    assert not strategy._entry_preserves_cycle_risk(state, state.quantity, Decimal(200))


def test_initial_order_no_longer_requires_fourteen_day_percentile():
    from nautilus_trader.test_kit.providers import TestInstrumentProvider

    targets = []

    class Observe(MeanReversionShort5MinStrategy):
        def apply_entry_target(self, state, target):
            targets.append(target)

        def _account_equity(self):
            return Decimal(500)

    strategy = Observe(make_config())
    strategy.instrument = TestInstrumentProvider.ethusdt_perp_binance()
    f = strategy.features
    f.entry_touch_allowed = lambda now: True
    f.entry_distance_allowed = lambda price: True
    f.bollinger = (90., 95., 100.)
    f.ma_distance.current_ma = 95.
    f.add_distance_mean = 7.
    assert f.stop_p995 is None and f.stop_ma_distance.current_ma is None
    strategy._refresh_initial_order(strategy.trade_state, STEP)
    assert len(targets) == 1 and targets[0].quantity > 0


@pytest.mark.parametrize('max_units', [1, 2])
def test_sizing_covers_initial_stop_before_second_entry(max_units):
    strategy = MeanReversionShort5MinStrategy(make_config(max_units=max_units))
    sizing = strategy.risk_model.size_initial_entry(
        -1, 100, 0, Decimal(500), Decimal('.001'), second_entry_price=110)
    first_cost = (Decimal(30) + Decimal(100) * Decimal('.0002')
                  + Decimal(130) * Decimal('.0005')) * Decimal('1.05')
    assert sizing['risk_per_quantity'] >= first_cost
    assert sizing['unit_quantity'] * first_cost <= Decimal(150)
    if max_units == 1:
        assert sizing['risk_per_quantity'] == first_cost


@pytest.mark.parametrize('average', ['0', '-1', 'NaN', 'Infinity'])
def test_invalid_average_does_not_produce_protective_stop(average):
    strategy = MeanReversionShort5MinStrategy(make_config())
    strategy.trade_state.average_price = Decimal(average)
    assert strategy._hard_stop_price(strategy.trade_state) == 0


@pytest.mark.parametrize('average,expected', [('98', '98'), ('105', '101.25')])
def test_remainder_stop_uses_current_average_and_half_sigma(average, expected):
    strategy = MeanReversionShort5MinStrategy(make_config())
    state = strategy.trade_state
    state.phase = 'remainder'
    state.average_price = Decimal(average)
    state.last_initial_fill_price = Decimal(70)
    state.entry_ma99 = Decimal(80)
    strategy.features.bollinger = (95., 100., 105.)
    strategy.features.stop_ma_distance.current_ma = 200.
    strategy.features.stop_p995 = 5.
    assert strategy._stop_target_price(state) == Decimal(expected)
    strategy.features.bollinger = (85., 90., 95.)
    assert strategy._stop_target_price(state) == Decimal('91.25')
    state.phase = 'scaled'
    assert strategy._stop_target_price(state) == Decimal(average) * Decimal("1.3")


def test_remainder_missing_boll_keeps_existing_stop():
    from types import SimpleNamespace

    strategy = MeanReversionShort5MinStrategy(make_config())
    state = strategy.trade_state
    state.phase = 'remainder'
    state.average_price = Decimal(105)
    state.entry_ma99 = Decimal(95)
    strategy.features.stop_ma_distance.current_ma = 200.
    strategy.features.stop_p995 = 5.
    strategy._cached_order = lambda key: SimpleNamespace(
        trigger_price=SimpleNamespace(as_decimal=lambda: Decimal('101.25')))
    assert strategy._hard_stop_price(state) == Decimal('101.25')
