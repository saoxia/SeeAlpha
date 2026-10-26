from decimal import Decimal
from itertools import pairwise

import pytest

from strategy_python.trend_following_short_5min.indicators import TrendLevels
from strategy_python.trend_following_short_5min.risk import SlopeRiskModel
from strategy_python.trend_following_short_5min.strategy import TrendFollowingShort5MinStrategy
from tests.strategy_python.trend_following_short_5min.strategy_test import (
    make_config as make_base_config,
)


def make_config(**overrides):
    # Risk experiments must opt in; production defaults use only the original protection lines.
    overrides.setdefault("tighten_exits_with_slope_risk", True)
    return make_base_config(**overrides)


@pytest.mark.parametrize(
    "slope,expected",
    [(0.2, 0), (0.1, 0), (0.05, 0.25), (0, 0.5), (-0.05, 0.75), (-0.1, 1), (-0.2, 1)],
)
def test_risk_mapping_is_bounded_continuous_and_directional(slope, expected):
    model = SlopeRiskModel()
    model.update(slope, 1)
    assert model.coefficient(1) == pytest.approx(expected)
    assert model.coefficient(-1) == pytest.approx(1 - expected)


def test_risk_smoothing_scale_invariance_zero_volatility_and_reset():
    a, b = SlopeRiskModel(), SlopeRiskModel()
    assert a.coefficient(1) == 0
    a.update(None, 1)
    assert a.smoothed_slope is None
    for slope, std in [(2, 10), (-2, 10), (-1, 10)]:
        a.update(slope, std)
        b.update(100 * slope, 100 * std)
        assert a.coefficient(1) == b.coefficient(1)
    assert a.smoothed_slope == pytest.approx(-0.05)
    a.update(0, 0)
    assert a.smoothed_slope == pytest.approx(-0.025)
    a.reset()
    assert a.smoothed_slope is None


class ProbeStrategy(TrendFollowingShort5MinStrategy):
    def cancel_entry(self, state):
        pass

    def submit_exit(self, state, *, half):
        self.exits.append(half)


@pytest.mark.parametrize("direction", [-1])
def test_risk_only_tightens_price_line_does_not_directly_exit_and_never_loosens(direction):
    strategy = ProbeStrategy(make_config(slope_risk_max_tightening_std=0.3))
    strategy.exits = []
    state = strategy.leg
    state.quantity = Decimal(1)
    levels = TrendLevels(100, 10, 120, 80, 102, 97, 98, 103, ma_slope=-direction)
    strategy.slope_risk.update(-direction, 10)  # maximum risk
    strategy._refresh_leg(state, levels, 110 if direction == 1 else 90)
    assert strategy.exits == []  # adverse slope alone cannot exit
    assert state.first_exit_level == Decimal(102 if direction == 1 else 95)
    assert state.final_exit_level == Decimal(100 if direction == 1 else 103)
    for _ in range(10):
        strategy.slope_risk.update(direction, 1)
    strategy._refresh_leg(state, levels, 110 if direction == 1 else 90)
    assert state.final_exit_level == Decimal(100 if direction == 1 else 103)
    assert state.first_exit_level == Decimal(102 if direction == 1 else 95)
    strategy._refresh_leg(state, levels, 100 if direction == 1 else 96)
    assert strategy.exits == [False]


@pytest.mark.parametrize("value", [-1, float("inf"), float("nan")])
def test_invalid_tightening_rejected(value):
    with pytest.raises(ValueError, match="收紧幅度"):
        ProbeStrategy(make_config(slope_risk_max_tightening_std=value))


def test_hard_exit_and_risk_cannot_be_enabled_together():
    with pytest.raises(ValueError, match="同时启用"):
        ProbeStrategy(make_config(exit_on_ma_slope_reversal=True))


@pytest.mark.parametrize("direction", [-1])
def test_distance_rule_preserves_price_buffer_and_is_monotonic(direction):
    from strategy_python.trend_following_short_5min.risk import tightened_level

    base, close, std = 100, 100 + direction * 10, 2
    lines = [
        tightened_level(
            base,
            close,
            std,
            direction,
            r,
            rule="distance",
            max_tightening_std=0.3,
            price_buffer_std=0.1,
            multiplier=1,
        )
        for r in [0, 0.25, 0.5, 0.75, 1]
    ]
    assert lines[0] == base
    assert lines[-1] == pytest.approx(close - direction * 0.2)
    assert all(direction * (b - a) >= 0 for a, b in pairwise(lines))
    assert all(direction * (close - line) >= 0.2 - 1e-12 for line in lines)


@pytest.mark.parametrize("direction", [-1])
def test_distance_rule_does_not_loosen_an_already_breached_base(direction):
    from strategy_python.trend_following_short_5min.risk import tightened_level

    assert (
        tightened_level(
            100,
            100 - direction,
            2,
            direction,
            1,
            rule="distance",
            max_tightening_std=0.3,
            price_buffer_std=0.1,
            multiplier=1,
        )
        == 100
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"slope_risk_rule": "unknown"},
        {"slope_risk_price_buffer_std": -0.1},
        {"slope_risk_price_buffer_std": float("nan")},
        {"slope_risk_short_multiplier": 1.1},
        {"slope_risk_short_multiplier": -0.1},
    ],
)
def test_invalid_risk_rule_parameters_are_rejected(overrides):
    with pytest.raises(ValueError):
        ProbeStrategy(make_config(**overrides))


@pytest.mark.parametrize(
    "profit,expected", [(-0.02, 1), (0, 1), (0.01, 0.5), (0.03, 0.25), (0.05, 1 / 6)]
)
def test_profit_multiplier_only_relaxes_profitable_positions(profit, expected):
    from strategy_python.trend_following_short_5min.risk import profit_risk_multiplier

    assert profit_risk_multiplier(profit, 0.01) == pytest.approx(expected)


@pytest.mark.parametrize("direction", [-1])
def test_profitable_positions_receive_less_tightening_in_both_directions(direction):
    positions = []
    for enabled in [False, True]:
        strategy = ProbeStrategy(make_config(profit_relaxes_slope_risk=enabled))
        strategy.exits = []
        state = strategy.leg
        state.record_entry_fill(Decimal(1), Decimal(100))
        close = 103 if direction == 1 else 97
        strategy.slope_risk.update(-direction, 10)  # full slope risk
        levels = TrendLevels(100, 10, 120, 80, 102, 97, 98, 103)
        strategy._refresh_leg(state, levels, close)
        positions.append(state.final_exit_level if direction == 1 else state.first_exit_level)
    # Profits reduce risk to 1/4; 1.5σ full shift becomes 0.375σ.
    assert float(positions[1]) == pytest.approx(100.75 if direction == 1 else 94.25)
    assert direction * (positions[0] - positions[1]) > 0


def test_actual_fill_cost_survives_partial_exit_and_clears_when_flat():
    from strategy_python.trend_following_short_5min.state import TrendLegState

    state = TrendLegState(1)
    state.record_entry_fill(Decimal(2), Decimal(100))
    state.record_entry_fill(Decimal(1), Decimal(103))
    assert state.quantity == 3 and state.average_entry_price == 101
    state.quantity -= Decimal(1)  # partial exit leaves remaining basis unchanged
    assert state.average_entry_price == 101
    state.clear()
    assert state.average_entry_price is None
    state.record_entry_fill(Decimal(1), Decimal(200))
    assert state.quantity == 1 and state.average_entry_price == 200


@pytest.mark.parametrize("scale", [0, -0.01, float("inf"), float("nan")])
def test_profit_scale_must_be_finite_positive(scale):
    with pytest.raises(ValueError, match="盈利放松尺度"):
        ProbeStrategy(make_config(profit_relaxation_scale=scale))
