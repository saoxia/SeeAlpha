"""Observable confirmation, cost economics, expiry and conservative study decisions."""

import json
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest

from strategy_python._research.mrl_entry_20260922.run import decision, experiments
from strategy_python.mean_reversion_long_entry_5min.admission import (
    FIVE_MINUTES,
    ReclaimAdmission,
    reward_cost_allowed,
)
from strategy_python.mean_reversion_long_entry_5min.preset import create_config, create_strategy
from strategy_python.mean_reversion_long_regime_5min.preset import create_config as prior_config


def bar(index, close):
    return SimpleNamespace(ts_end=index * FIVE_MINUTES, close=close)


def test_reclaim_uses_band_known_before_breach_not_recomputed_after_recovery():
    model = ReclaimAdmission()
    model.update(bar(1, 101), None)
    assert model.update(bar(2, 99), (100, 110, 120)) is None
    quote = model.update(bar(3, 103), (95, 105, 115))
    assert quote.bands == (100, 110, 120)
    assert quote.created_ns == 3 * FIVE_MINUTES
    assert quote.expires_ns == 4 * FIVE_MINUTES
    assert model.current(quote.created_ns - 1) is None
    assert model.current(quote.created_ns) == quote
    assert model.current(quote.expires_ns) is None
    assert model.update(bar(4, 104), (95, 105, 115)) is None


@pytest.mark.parametrize("close", [99, 100, 110, 111])
def test_touch_continuation_or_already_reaching_target_does_not_confirm(close):
    model = ReclaimAdmission()
    model.update(bar(1, 101), None)
    model.update(bar(2, 99), (100, 110, 120))
    assert model.update(bar(3, close), (95, 105, 115)) is None


def test_gap_clears_setup_and_does_not_create_breach_from_stale_bands():
    model = ReclaimAdmission()
    model.update(bar(1, 101), None)
    model.update(bar(2, 99), (100, 110, 120))
    assert model.update(bar(4, 99), (100, 110, 120)) is None
    assert model.update(bar(5, 103), (100, 110, 120)) is None
    with pytest.raises(ValueError, match="increase"):
        model.update(bar(5, 99), (100, 110, 120))
    model.reset()
    assert model.last_end is model.quote is model.breach_bands is None


def test_cost_gate_charges_target_notional_and_accounts_for_higher_fees():
    # A 0.2% gross target is not enough to cover twice a 0.1%-each-side round trip.
    assert not reward_cost_allowed(100, Decimal("100.2"), 0.001, 0, 2)
    # Exactly 0.4% is also insufficient: the exit fee is on a larger notional.
    assert not reward_cost_allowed(100, Decimal("100.4"), 0.001, 0, 2)
    assert reward_cost_allowed(100, Decimal("100.5"), 0.001, 0, 2)
    assert reward_cost_allowed(100, Decimal("100.4"), 0.0005, 2, 2)
    assert not reward_cost_allowed(100, Decimal("100.4"), 0.001, 2, 2)
    assert not reward_cost_allowed(100, 100, 0, 0, 2)
    assert not reward_cost_allowed(100, float("nan"), 0.001, 0, 2)


def test_control_keeps_original_bounded_settings_and_same_budget_for_all_arms():
    start = datetime(2022, 5, 1, tzinfo=UTC)
    prior = json.loads(prior_config(start, variant="bounded").json())
    control = json.loads(create_config(start, variant="control").json())
    for name, value in prior.items():
        if name.startswith("regime_"):
            continue
        assert control[name] == value, name
    assert not control["entry_cost_filter"] and not control["entry_reclaim_filter"]
    for variant in ("control", "cost", "reclaim"):
        strategy = create_strategy(start, asset="ETH", variant=variant)
        assert strategy.config.max_units == 1
        assert strategy.config.cycle_risk_fraction == 0.075
        assert strategy.config.hard_stop_distance == 0.25
        assert strategy.config.cycle_time_exit_hours == 24
        assert strategy.config.cycle_band_stop_multiple == 2


def records():
    rows = {
        r["id"]: {
            "net_pnl": 10.0,
            "review_marked_pnl": 2.0,
            "trades": 200,
            "review_closed_trades": 50,
            "pnl_without_best5": 1.0,
            "max_drawdown_pct": 1.0,
            "invalid_entry_orders": 0,
            "off_tick_fields": 0,
            "cashflow_error": 0.0,
        }
        for r in experiments()
    }
    baselines = {
        f"{asset}_baseline_fee{fee}": {
            "net_pnl": -30.0,
            "review_marked_pnl": -10.0,
            "max_drawdown_pct": 10.0,
        }
        for asset in ("BTC", "ETH")
        for fee in (0, 5)
    }
    return rows, baselines


def test_strict_execution_or_recent_sample_failure_prevents_promotion():
    rows, baselines = records()
    assert decision(rows, baselines)["passed"]
    rows["ETH_reclaim_fee5_strict"]["net_pnl"] = -1
    assert not decision(rows, baselines)["passed"]
    rows["ETH_reclaim_fee5_strict"]["net_pnl"] = 10
    rows["ETH_reclaim_fee0"]["review_closed_trades"] = 29
    assert not decision(rows, baselines)["passed"]


def test_consistent_loss_reduction_is_distinguished_from_positive_evidence():
    rows, baselines = records()
    for row in rows.values():
        row.update(net_pnl=-5.0, review_marked_pnl=-2.0)
    result = decision(rows, baselines)
    assert result["consistent_improvement"] and not result["passed"]
    assert not result["default_changed"]


def test_strategy_reset_cannot_reuse_old_confirmation_or_exit_locks():
    strategy = create_strategy()
    strategy.entry_admission.update(bar(1, 101), None)
    strategy.entry_admission.update(bar(2, 99), (100, 110, 120))
    strategy.entry_admission.update(bar(3, 103), (95, 105, 115))
    strategy.entry_quote_expires_ns = 4 * FIVE_MINUTES
    strategy._cycle_exiting.add(1)
    strategy._cycle_rearm.add(1)
    strategy.on_reset()
    assert strategy.entry_admission.current(3 * FIVE_MINUTES) is None
    assert strategy.entry_quote_expires_ns is None
    assert not strategy._cycle_exiting and not strategy._cycle_rearm
