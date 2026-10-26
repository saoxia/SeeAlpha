"""Cost stress changes actual execution fees; cash-flow metrics include all commissions."""

from decimal import Decimal
from types import SimpleNamespace

import pandas as pd
import pytest

from strategy_python._research.tfs_stability_20260921.study import (
    experiments,
    fee_instrument,
    measure,
)


def test_region_gate_rejects_isolated_peak_and_time_failure():
    from strategy_python._research.tfs_stability_20260921.report import gate

    rows = []
    for experiment in experiments():
        if experiment["kind"] != "grid":
            continue
        center = experiment["stop_scale"] == 1 and experiment["lock"] == 0.8
        rows.append(
            {
                **experiment,
                "net_pnl": 1 if center else -1,
                "review_marked_pnl": 1,
                "trades": 70,
                "review_closed_trades": 30,
                "entry_fills_below_snapshot_min_notional": 0,
            }
        )
    assert not gate(pd.DataFrame(rows))["passed"]
    # A connected 2x2 in both markets and cost scenarios qualifies under the frozen rules.
    for row in rows:
        region = row["stop_scale"] in (1.0, 1.1) and row["lock"] in (0.8, 0.85)
        row["net_pnl"] = 1 if region else -1
    assert gate(pd.DataFrame(rows))["passed"]
    for row in rows:
        if row["asset"] == "ETH":
            row["review_marked_pnl"] = -1
    assert not gate(pd.DataFrame(rows))["passed"]


def test_fee_stress_clones_instrument_and_adds_both_sides():
    from nautilus_support import TestInstrumentProvider, instrument_fee_rates
    original = TestInstrumentProvider.ethusdt_perp_binance()
    changed = fee_instrument(original, 5)
    assert instrument_fee_rates(changed) == (Decimal("0.0007"), Decimal("0.0009"))
    assert instrument_fee_rates(original) == (Decimal("0.0002"), Decimal("0.0004"))
    assert changed.price_increment == original.price_increment
    assert changed is not original


def test_grid_keeps_same_values_for_both_markets_and_no_missing_cost_cells():
    rows = experiments()
    assert len(rows) == len({r["id"] for r in rows}) == 106
    signatures = [
        {
            (r["kind"], r["stop_scale"], r["lock"], r["extra_fee_bp"], r["starting_balance"])
            for r in rows
            if r["asset"] == asset
        }
        for asset in ("BTC", "ETH")
    ]
    assert signatures[0] == signatures[1]


def test_metrics_use_net_position_cash_flow_and_mark_open_exposure(tmp_path):
    stamps = pd.date_range("2024-12-31 23:59", periods=3, freq="min", tz="UTC")
    # Short 1 @100, mark rises to110 (unrealized -10), close @90; fees 1 each side.
    fills = pd.DataFrame(
        {
            "status": ["FILLED", "FILLED"],
            "commissions": [["1 USDT"], "['1 USDT']"],
            "side": ["SELL", "BUY"],
            "filled_qty": [1, 1],
            "avg_px": [100, 90],
            "ts_last": [stamps[0], stamps[2]],
            "position_id": ["p", "p"],
        }
    )
    trader = SimpleNamespace(
        generate_order_fills_report=lambda: fills,
        generate_positions_report=lambda: pd.DataFrame(),
        generate_account_report=lambda venue: pd.DataFrame(),
    )
    account = SimpleNamespace(
        balance_total=lambda currency: SimpleNamespace(as_double=lambda: 508.0)
    )
    engine = SimpleNamespace(
        **vars(trader), cache=SimpleNamespace(account_for_venue=lambda venue: account)
    )
    result = measure(
        engine,
        pd.Series([100, 110, 90], index=stamps),
        stamps[0],
        stamps[-1] + pd.Timedelta(minutes=1),
        tmp_path,
        500,
    )
    assert result["net_pnl"] == 8
    assert result["fees"] == 2
    assert result["trades"] == 1
    assert result["max_drawdown_pct"] == pytest.approx(2.2)
    assert result["review_marked_pnl"] == 9  # Dec31 equity499 -> final508, not reset500.
    assert result["pnl_without_best5"] == 0
