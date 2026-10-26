"""Causal trend admission, bounded-cycle behavior, and baseline preservation."""

import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pandas as pd
import pytest

from strategy_python._indicator.bars import NANOSECONDS_PER_DAY as DAY
from strategy_python._indicator.bars import NANOSECONDS_PER_HOUR as HOUR
from strategy_python._mechanism.rules import mean_reversion_snapshot
from strategy_python._research.mrl_regime_20260922.accounting import measure
from strategy_python._research.mrl_regime_20260922.run import decide, experiments
from strategy_python.btc_cycle_optimized_20260915_mrl_original.preset import (
    create_config as baseline_config,
)
from strategy_python.mean_reversion_long_regime_5min.preset import create_config, create_strategy
from strategy_python.mean_reversion_long_regime_5min.regime import DailyRegime
from strategy_python.mean_reversion_long_regime_5min.strategy import (
    RegimeMeanReversionLong5MinStrategy as Strategy,
)


def test_daily_filter_waits_for_full_past_history_and_rejects_downtrend():
    regime = DailyRegime(3, 2)
    for i in range(1, 5):
        assert not regime.update(i * DAY, 100 + i)
    assert regime.update(5 * DAY, 105)
    assert regime.allows_entry(5 * DAY)
    assert not regime.allows_entry(5 * DAY - 1)
    assert not regime.update(6 * DAY, 90)
    assert not regime.allows_entry(6 * DAY)


def test_daily_gap_and_stale_state_cannot_reuse_a_bullish_label():
    regime = DailyRegime(3, 2)
    for i in range(1, 6):
        regime.update(i * DAY, 100 + i)
    assert regime.allows_entry(6 * DAY)
    assert not regime.allows_entry(6 * DAY + 1)
    assert not regime.update(7 * DAY, 110)
    assert len(regime.closes) == 1
    with pytest.raises(ValueError, match="increase"):
        regime.update(7 * DAY, 110)
    with pytest.raises(ValueError, match="completed"):
        regime.update(8 * DAY - 1, 110)


def test_gate_cancels_old_entry_without_touching_account_or_protection():
    cancelled = []
    owner = SimpleNamespace(
        config=SimpleNamespace(regime_filter_enabled=True),
        daily_regime=DailyRegime(),
        regime_checks=0,
        regime_blocked=0,
        _cancel_entry=lambda state: cancelled.append(state),
    )
    state = object()
    Strategy._refresh_initial_order(owner, state, 100 * DAY)
    assert cancelled == [state]
    assert owner.regime_checks == owner.regime_blocked == 1


@pytest.mark.parametrize(
    "elapsed,close,reason",
    [
        (24 * HOUR - 1, 100.0, None),
        (24 * HOUR, 100.0, "cycle_time_exit"),
        (HOUR, 97.9, "cycle_structure_exit"),
    ],
)
def test_cycle_exit_uses_first_fill_time_and_frozen_entry_width(elapsed, close, reason):
    submitted, cancelled = [], []
    state = SimpleNamespace(
        invested=True,
        position_start_ns=DAY,
        direction=1,
        average_price=100.0,
        cycle_snapshot=mean_reversion_snapshot(100, 101, (0.3, 0.9, 2.3)),
    )
    owner = SimpleNamespace(
        legs=[state],
        config=SimpleNamespace(
            cycle_time_exit_hours=24, cycle_band_stop_multiple=2, cycle_rearm_after_exit=True
        ),
        _cycle_exiting=set(),
        _cycle_rearm=set(),
        _cancel_state_orders=lambda state: cancelled.append(state),
        _submit_emergency_exit=lambda state, why, **kw: submitted.append((why, kw)),
    )
    Strategy._check_cycle_exits(owner, SimpleNamespace(ts_event=DAY + elapsed, close=close))
    assert submitted == ([] if reason is None else [(reason, {"role": "cycle_exit"})])
    assert bool(cancelled) == bool(reason)
    assert owner._cycle_rearm == (set() if reason is None else {1})


def test_reentry_waits_until_closed_five_minute_price_recovers_to_middle():
    cancelled, attempted = [], []
    state = SimpleNamespace(direction=1, invested=False)
    owner = SimpleNamespace(
        features=SimpleNamespace(
            on_five_minute_bar=lambda bar: (99, 100, 101), bollinger=(99, 100, 101)
        ),
        legs=[state],
        _cycle_rearm={1},
        _cycle_exiting=set(),
        _cancel_entry=lambda s: cancelled.append(s),
        _refresh_initial_order=lambda s, t: attempted.append(t),
    )
    Strategy._on_five_minute_bar(owner, SimpleNamespace(close=99, ts_end=DAY))
    assert cancelled == [state] and not attempted
    Strategy._on_five_minute_bar(owner, SimpleNamespace(close=100, ts_end=DAY + HOUR))
    assert attempted == [DAY + HOUR] and owner._cycle_rearm == set()


def test_same_boundary_entry_reads_prior_daily_state():
    order = []
    owner = SimpleNamespace(
        config=SimpleNamespace(bar_type="one_minute"),
        instrument=object(),
        legs=[],
        _check_cycle_exits=lambda bar: None,
        features=SimpleNamespace(
            aggregate=lambda bar: {5: object(), 15: None, 360: None, 1440: object(), 60: None}
        ),
        _on_five_minute_bar=lambda bar: order.append("entry"),
        _on_daily_bar=lambda bar: order.append("daily_update"),
    )
    Strategy.on_bar(owner, SimpleNamespace(bar_type="one_minute"))
    assert order == ["entry", "daily_update"]


def test_disabled_candidate_preserves_every_original_config_and_risk_budget():
    start = datetime(2021, 1, 1, tzinfo=UTC)
    original = json.loads(baseline_config(start).json())
    candidate = json.loads(create_config(start, variant="baseline").json())
    for key, value in original.items():
        assert candidate[key] == value, key
    for variant in ("regime", "bounded", "combined"):
        strategy = create_strategy(start, asset="ETH", variant=variant)
        assert strategy.config.max_units == 1
        assert strategy.config.cycle_risk_fraction == original["cycle_risk_fraction"]
        assert strategy.config.hard_stop_distance == original["hard_stop_distance"]
        assert str(strategy.config.instrument_id) == "ETHUSDT-PERP.BINANCE"


def test_single_asset_or_cost_failure_prevents_main_candidate_promotion():
    rows = {
        r["id"]: {
            "net_pnl": 1,
            "review_marked_pnl": 1,
            "trades": 100,
            "review_closed_trades": 30,
            "max_drawdown_pct": 1,
            "invalid_entry_orders": 0,
            "off_tick_fields": 0,
            "cashflow_error": 0,
        }
        for r in experiments()
    }
    assert decide(rows)["main_passed"]
    rows["ETH_combined_fee5"]["review_marked_pnl"] = -1
    result = decide(rows)
    assert not result["main_passed"]
    assert result["variants"]["regime"]["passed"]
    assert not result["default_strategy_changed"] and not result["live_trading_authorized"]


def test_no_trades_is_a_valid_zero_return_without_profitable_evidence(tmp_path):
    engine = SimpleNamespace(
        generate_order_fills_report=lambda: pd.DataFrame(),
            generate_positions_report=lambda: pd.DataFrame(),
            generate_account_report=lambda venue: pd.DataFrame({"total": [500]}),
        cache=SimpleNamespace(
            account_for_venue=lambda venue: SimpleNamespace(
                balance_total=lambda currency: SimpleNamespace(as_double=lambda: 500)
            )
        ),
    )
    start = pd.Timestamp("2025-01-01", tz="UTC")
    end = start + pd.Timedelta(minutes=2)
    marks = pd.Series([100.0, 90.0], index=pd.date_range(start, periods=2, freq="min"))
    result = measure(engine, marks, start, end, tmp_path, 500)
    assert result["trades"] == result["net_pnl"] == result["max_drawdown_pct"] == 0
    assert result["cashflow_error"] == result["missing_minutes"] == 0
    assert result["win_rate_pct"] is None and result["profit_factor"] is None
    assert pd.read_csv(tmp_path / "trades.csv").empty
    assert pd.read_csv(tmp_path / "daily_equity.csv").equity.tolist() == [500]
