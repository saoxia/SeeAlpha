from decimal import Decimal

import pytest

from nautilus_trader.test_kit.providers import TestInstrumentProvider

from strategy_python.mean_reversion_short_5min import MeanReversionShort5MinStrategy
from strategy_python.mean_reversion_short_5min.features import BollingerTouchFilter
from tests.strategy_python.mean_reversion_short_5min.strategy_test import completed_bar, make_config

def test_five_minute_filter_requires_latest_lower_touch():
    f = BollingerTouchFilter(5, 20, 2.)
    f._bollinger.update = lambda close: (90., 100., 110.)
    step = 5 * 60_000_000_000
    for i, (high, low, expected) in enumerate([
        (105, 95, False), (105, 90, True), (110, 95, False),
        (105, 95, False), (105, 90, True), (110, 90, False),
    ], 1):
        f.update(completed_bar(i * step, high=high, low=low, close=100, minutes=5))
        assert f.allowed(i * step) is expected


def test_five_minute_upper_touch_cancels_pending_initial():
    targets, cancellations = [], []

    class Observe(MeanReversionShort5MinStrategy):
        def apply_entry_target(self, state, target):
            targets.append(target)

        def _cancel_entry(self, state):
            cancellations.append(state.entry_order_id)

        def _account_equity(self):
            return Decimal(500)

    strategy = Observe(make_config())
    strategy.instrument = TestInstrumentProvider.ethusdt_perp_binance()
    f = strategy.features
    f.entry_filters[5]._bollinger.update = lambda close: (90., 100., 110.)
    f.bollinger = (90., 95., 100.)
    f.ma_distance.current_ma = f.stop_ma_distance.current_ma = 95.
    f.stop_p995, f.add_distance_mean = 40., 10.
    f.entry_distance_allowed = lambda price: True
    step = 5 * 60_000_000_000
    for i in range(-34, 1):
        f.entry_filters[5].update(completed_bar(i * step, high=110, low=90, close=100, minutes=5))
    f.entry_filters[5].update(completed_bar(step, high=105, low=90, close=100, minutes=5))
    strategy._refresh_initial_order(strategy.trade_state, step)
    assert len(targets) == 1
    strategy.trade_state.entry_order_id = 'pending'
    f.entry_filters[5].update(completed_bar(2 * step, high=110, low=95, close=100, minutes=5))
    strategy._refresh_initial_order(strategy.trade_state, 2 * step)
    assert cancellations == ['pending'] and len(targets) == 1
    f.entry_filters[5].update(completed_bar(3 * step, high=105, low=90, close=100, minutes=5))
    strategy._refresh_initial_order(strategy.trade_state, 3 * step)
    assert len(targets) == 2
    assert set(f.entry_filters) == {5}
    for i in range(4, 39):
        f.entry_filters[5].update(
            completed_bar(i * step, high=105, low=95, close=100, minutes=5)
        )
    assert f.entry_filters[5].allowed(38 * step)
    strategy._refresh_initial_order(strategy.trade_state, 38 * step)
    assert cancellations == ['pending', 'pending'] and len(targets) == 2


def test_gap_duplicate_and_reset_clear_five_minute_filter():
    f = BollingerTouchFilter(5, 2, 2.)
    step = 5 * 60_000_000_000
    f.update(completed_bar(step, high=105, low=95, close=100, minutes=5))
    f.update(completed_bar(2 * step, high=95, low=70, close=90, minutes=5))
    assert f.allowed(2 * step)
    f.update(completed_bar(2 * step, high=120, low=90, close=100, minutes=5))
    assert f.allowed(2 * step)
    assert not f.allowed(3 * step)
    f.update(completed_bar(4 * step, high=105, low=95, close=100, minutes=5))
    assert f.bollinger is None and f.lower_touch_ns is None
    f.reset()
    assert f.last_bar_ns is None and not f.allowed(4 * step)


@pytest.mark.parametrize('touches,expected', [
    ([(105, 90)] * 2, False),
    ([(105, 90)] * 3, False),
    ([(110, 95)] * 3, False),
    ([(110, 95)] * 3 + [(105, 90)], False),
    ([(110, 95)] + [(105, 90)] * 3, False),
    ([(110, 95)] * 2 + [(105, 90)] * 2, True),
    ([(110, 90), (105, 90)], False),
    ([(110, 90)] * 2, True),
])
def test_36_bar_frequency_counts_both_sides_including_exact_touches(touches, expected):
    f = BollingerTouchFilter(5, 20, 2.)
    f._bollinger.update = lambda close: (90., 100., 110.)
    step = 5 * 60_000_000_000
    bars = touches + [(105, 95)] * (36 - len(touches))
    for i, (high, low) in enumerate(bars, 1):
        f.update(completed_bar(i * step, high=high, low=low, close=100, minutes=5))
        if i < 36:
            assert not f.frequency_allowed()
    assert f.frequency_allowed() is expected


def test_frequency_window_expires_touches_and_ignores_duplicates_then_resets_on_gap():
    f = BollingerTouchFilter(5, 20, 2.)
    f._bollinger.update = lambda close: (90., 100., 110.)
    step = 5 * 60_000_000_000
    for i in range(1, 37):
        high, low = (110, 90) if i in (1, 2) else (105, 95)
        f.update(completed_bar(i * step, high=high, low=low, close=100, minutes=5))
    assert f.frequency_allowed()
    counts_before = list(f.touch_counts)
    f.update(completed_bar(36 * step, high=110, low=90, close=100, minutes=5))
    assert list(f.touch_counts) == counts_before
    f.update(completed_bar(37 * step, high=105, low=95, close=100, minutes=5))
    assert not f.frequency_allowed() and f.touch_counts[0] == (1, 1)
    f.update(completed_bar(39 * step, high=110, low=90, close=100, minutes=5))
    assert len(f.touch_counts) == 1 and not f.frequency_allowed()
    f.reset()
    assert not f.touch_counts and not f.frequency_allowed()


def test_frequency_requires_bollinger_ready_and_clears_invalid_data():
    f = BollingerTouchFilter(5, 20, 2.)
    step = 5 * 60_000_000_000
    for i in range(1, 55):
        f.update(completed_bar(i * step, high=110, low=90, close=100, minutes=5))
    assert len(f.touch_counts) == 35 and not f.frequency_allowed()
    f.update(completed_bar(55 * step, high=110, low=90, close=100, minutes=5))
    assert f.frequency_allowed()
    f.update(completed_bar(56 * step, high=float('nan'), low=90, close=100, minutes=5))
    assert not f.touch_counts and not f.frequency_allowed()


@pytest.mark.parametrize('sides,expected', [
    ('UUU', False), ('LUU', True), ('ULU', True), ('UUL', True),
    ('LLL', True), ('BUU', True), ('BB', False), ('LUUU', False),
])
def test_last_three_touching_candles_require_a_lower_touch(sides, expected):
    f = BollingerTouchFilter(5, 20, 2.)
    f._bollinger.update = lambda close: (90., 100., 110.)
    step = 5 * 60_000_000_000
    for i, side in enumerate(sides, 1):
        high, low = {'U': (110, 95), 'L': (105, 90), 'B': (110, 90)}[side]
        f.update(completed_bar((2*i-1)*step, high=high, low=low, close=100, minutes=5))
        # Non-touching candles do not consume a touch record.
        f.update(completed_bar(2*i*step, high=105, low=95, close=100, minutes=5))
    assert len(f.recent_touches) == min(3, len(sides))
    assert f.recent_lower_touch_allowed() is expected


def test_recent_touch_history_ignores_duplicates_and_resets_on_missing_data():
    f = BollingerTouchFilter(5, 20, 2.)
    f._bollinger.update = lambda close: (90., 100., 110.)
    step = 5 * 60_000_000_000
    for i in range(1, 4):
        f.update(completed_bar(i*step, high=110, low=90, close=100, minutes=5))
    assert f.recent_lower_touch_allowed()
    before = list(f.recent_touches)
    f.update(completed_bar(3*step, high=110, low=95, close=100, minutes=5))
    assert list(f.recent_touches) == before
    f.update(completed_bar(5*step, high=110, low=95, close=100, minutes=5))
    assert list(f.recent_touches) == [(1, 0)] and not f.recent_lower_touch_allowed()
    f.update(completed_bar(6*step, high=float('nan'), low=90, close=100, minutes=5))
    assert not f.recent_touches
    f.update(completed_bar(7*step, high=110, low=90, close=100, minutes=5))
    f.reset()
    assert not f.recent_touches


def test_recent_touch_gate_is_wired_into_combined_entry_filter(monkeypatch):
    from strategy_python.mean_reversion_short_5min.features import EthFeatureModel

    features = EthFeatureModel(make_config())
    f = features.entry_filters[5]
    # Isolate the new gate from the other independently tested entry filters.
    monkeypatch.setattr(f, 'allowed', lambda now: True)
    monkeypatch.setattr(f, 'frequency_allowed', lambda: True)
    f.recent_touches.extend([(1, 0)] * 3)
    assert not features.entry_touch_allowed(0)
    f.recent_touches.append((0, 1))
    assert features.entry_touch_allowed(0)
