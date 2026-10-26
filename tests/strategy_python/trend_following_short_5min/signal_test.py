from decimal import Decimal

import pytest

from strategy_python.trend_following_short_5min.signal import ExitAction, evaluate_exit
from strategy_python.trend_following_short_5min.state import TrendLegState


def invested_state(direction: int) -> TrendLegState:
    state = TrendLegState(direction=direction, quantity=Decimal(10))
    if direction > 0:
        state.first_exit_level = Decimal(102)
        state.final_exit_level = Decimal(97)
    else:
        state.first_exit_level = Decimal(98)
        state.final_exit_level = Decimal(103)
    return state


def test_long_exits_half_then_all() -> None:
    state = invested_state(1)
    assert evaluate_exit(state, 103) is None
    assert evaluate_exit(state, 100) == ExitAction.HALF
    state.first_exit_done = True
    assert evaluate_exit(state, 100) is None
    assert evaluate_exit(state, 96) == ExitAction.ALL


def test_large_move_skips_directly_to_full_exit() -> None:
    assert evaluate_exit(invested_state(1), 96) == ExitAction.ALL
    assert evaluate_exit(invested_state(-1), 104) == ExitAction.ALL


def test_short_exit_is_mirrored() -> None:
    state = invested_state(-1)
    assert evaluate_exit(state, 97) is None
    assert evaluate_exit(state, 100) == ExitAction.HALF
    assert evaluate_exit(state, 104) == ExitAction.ALL


def test_long_exit_levels_only_ratchet_up() -> None:
    state = TrendLegState(1)
    state.ratchet_exits(Decimal(100), Decimal(90))
    state.ratchet_exits(Decimal(98), Decimal(88))
    assert state.first_exit_level == Decimal(100)
    assert state.final_exit_level == Decimal(90)
    state.ratchet_exits(Decimal(103), Decimal(93))
    assert state.first_exit_level == Decimal(103)
    assert state.final_exit_level == Decimal(93)


def test_short_exit_levels_only_ratchet_down() -> None:
    state = TrendLegState(-1)
    state.ratchet_exits(Decimal(100), Decimal(110))
    state.ratchet_exits(Decimal(102), Decimal(112))
    assert state.first_exit_level == Decimal(100)
    assert state.final_exit_level == Decimal(110)
    state.ratchet_exits(Decimal(97), Decimal(107))
    assert state.first_exit_level == Decimal(97)
    assert state.final_exit_level == Decimal(107)


def test_reentry_needs_both_cooldown_and_return_to_ma_zone() -> None:
    state = TrendLegState(1)
    state.lock_reentry(2)

    assert not state.observe_reentry_zone(Decimal(110), Decimal(100), Decimal(2), Decimal("0.2"))
    assert not state.observe_reentry_zone(Decimal(109), Decimal(100), Decimal(2), Decimal("0.2"))
    assert state.observe_reentry_zone(Decimal("100.3"), Decimal(100), Decimal(2), Decimal("0.2"))


def test_loss_stop_is_strictly_beyond_half_percent_for_both_directions():
    for direction in (1, -1):
        state = TrendLegState(direction)
        state.record_entry_fill(Decimal(1), Decimal(100))
        # No protection lines: the loss stop is independent.
        for loss in (Decimal("0.49"), Decimal("0.50")):
            assert (
                evaluate_exit(state, float(100 - direction * loss), max_loss_return=0.005) is None
            )
        assert (
            evaluate_exit(state, float(100 - direction * Decimal("0.51")), max_loss_return=0.005)
            == ExitAction.ALL
        )
        assert evaluate_exit(state, float(100 + direction), max_loss_return=0.005) is None


def test_loss_stop_has_priority_over_partial_exit_and_applies_to_remaining_position():
    state = invested_state(1)
    state.average_entry_price = Decimal(100)
    assert evaluate_exit(state, 99.49, max_loss_return=None) == ExitAction.HALF
    assert evaluate_exit(state, 99.49, max_loss_return=0.005) == ExitAction.ALL
    state.first_exit_done = True
    state.quantity = Decimal(5)
    assert evaluate_exit(state, 99.49, max_loss_return=0.005) == ExitAction.ALL
    state.clear()
    assert evaluate_exit(state, 90, max_loss_return=0.005) is None


def test_profit_activation_is_inclusive_latched_and_reset_for_both_directions():
    for direction in (1, -1):
        state = TrendLegState(direction)
        state.record_entry_fill(Decimal(1), Decimal(100))
        # Original lines would already exit at these prices.
        state.first_exit_level = Decimal(110 if direction == 1 else 90)
        state.final_exit_level = Decimal(95 if direction == 1 else 105)
        args = {
            "max_loss_return": 0.005,
            "take_profit_activation_return": 0.02,
            "full_exit_on_first": direction < 0,
        }
        assert evaluate_exit(state, 100 + direction * 1.99, **args) is None
        assert not state.dynamic_exit_armed
        expected = ExitAction.HALF if direction == 1 else ExitAction.ALL
        assert evaluate_exit(state, 100 + direction * 2, **args) == expected
        assert state.dynamic_exit_armed
        # Falling back below 2% does not disable the already armed protection.
        assert evaluate_exit(state, 100 + direction, **args) == expected
        state.clear()
        assert not state.dynamic_exit_armed
        state.record_entry_fill(Decimal(1), Decimal(200))
        assert evaluate_exit(state, 200 + direction * 3.99, **args) is None
        assert not state.dynamic_exit_armed


def test_loss_stop_remains_active_before_and_after_profit_activation():
    for armed in (False, True):
        state = TrendLegState(1)
        state.record_entry_fill(Decimal(1), Decimal(100))
        state.dynamic_exit_armed = armed
        assert (
            evaluate_exit(state, 99.49, max_loss_return=0.005, take_profit_activation_return=0.02)
            == ExitAction.ALL
        )


def test_reaching_profit_threshold_only_arms_does_not_force_exit():
    state = TrendLegState(1)
    state.record_entry_fill(Decimal(1), Decimal(100))
    state.first_exit_level = Decimal(101)
    state.final_exit_level = Decimal(99)
    assert evaluate_exit(state, 102, take_profit_activation_return=0.02) is None
    assert state.dynamic_exit_armed


@pytest.mark.parametrize("direction", [-1])
def test_reverse_cross_exit_respects_activation_and_ignores_ma_lines(direction):
    from decimal import Decimal

    from strategy_python.trend_following_short_5min.signal import ExitAction, evaluate_exit
    from strategy_python.trend_following_short_5min.state import TrendLegState

    state = TrendLegState(direction)
    state.record_entry_fill(Decimal(1), Decimal(100))
    state.first_exit_level = state.final_exit_level = Decimal(200 if direction == 1 else 1)
    assert (
        evaluate_exit(
            state, 100, take_profit_activation_return=0.02, reverse_cross_signal=-direction
        )
        is None
    )
    # Arm without a reverse crossing: old MA protection lines must not exit.
    assert (
        evaluate_exit(
            state, 100 + direction * 3, take_profit_activation_return=0.02, reverse_cross_signal=0
        )
        is None
    )
    assert state.dynamic_exit_armed
    assert (
        evaluate_exit(
            state, 100, take_profit_activation_return=0.02, reverse_cross_signal=-direction
        )
        == ExitAction.ALL
    )
