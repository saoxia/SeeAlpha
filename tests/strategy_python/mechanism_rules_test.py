"""Candidate entry snapshots preserve units, timing, and planned price risk."""

import pytest

from strategy_python._mechanism.rules import (
    HourlyATR,
    mean_reversion_snapshot,
    pullback_stop,
    trend_snapshot,
)


def test_stop_expansion_reduces_notional_and_does_not_mutate_old_snapshot():
    first = trend_snapshot(100, 1, 1.5, 2.2)
    second = trend_snapshot(100, 2, 1.5, 2.2)
    assert first.stop_return * first.notional_fraction == pytest.approx(0.005)
    assert second.stop_return * second.notional_fraction == pytest.approx(0.005)
    assert first.stop_return == pytest.approx(0.015)
    assert second.notional_fraction == pytest.approx(first.notional_fraction / 2)
    scaled_prices = trend_snapshot(10000, 100, 1.5, 2.2)
    assert scaled_prices == first
    assert trend_snapshot(100, None, 1.5, 2.2) is None


def test_atr_gap_requires_new_complete_history():
    model = HourlyATR(2)
    hour = 3600_000_000_000
    for t in (1, 2, 3):
        model.update(t * hour, 101, 99, 100)
    assert model.value == 2
    model.update(5 * hour, 101, 99, 100)
    assert model.value is None
    model.update(6 * hour, 101, 99, 100)
    assert model.value is None
    model.update(7 * hour, 101, 99, 100)
    assert model.value == 2


def test_mrl_scales_to_current_band_and_respects_cost_floor():
    first = mean_reversion_snapshot(100, 101, [0.3, 0.9, 2.3])
    second = mean_reversion_snapshot(10000, 10100, [0.3, 0.9, 2.3])
    assert first == second
    assert first.take_profit_return == pytest.approx(0.01)
    narrow = mean_reversion_snapshot(100, 100.001, [0.3, 0.9, 2.3])
    assert all(distance == 0.0004 for _, distance in narrow.add_offsets)


def test_pbs_zero_estimated_buffer_still_places_stop_above_structure():
    assert pullback_stop(100, 102, 1, 0.1, 0, 1, 3) == pytest.approx(102.1)
    assert pullback_stop(100, 104, 1, 0.1, 0, 1, 3) is None


def test_pbs_decimal_tick_survives_exchange_rounding():
    from decimal import ROUND_DOWN, Decimal

    for units in range(500000, 500200):
        high = units / 10
        stop = pullback_stop(high - 20, high, 10, 0.1, 0, 1, 3)
        rounded = (Decimal(str(stop)) / Decimal(".1")).to_integral_value(
            rounding=ROUND_DOWN
        ) * Decimal(".1")
        assert rounded >= Decimal(str(high)) + Decimal(".1")
