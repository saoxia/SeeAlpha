"""Behavioral checks for causality, restart, fill evidence and the locked review."""

import json
import math
from decimal import Decimal
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from forward_validation import core, review, service
from forward_validation.core import DAY, HOUR, MINUTE
from strategy_python._indicator.bars import CompletedBarAggregator
from strategy_python.btc_cycle_optimized_20260915_mrl_original.features import EthFeatureModel

BASE = 20_000 * DAY
RULES = {
    "tick": "0.1",
    "step": "0.001",
    "min_qty": "0.001",
    "max_qty": "1000",
    "min_notional": "100",
}
MANIFEST = {"start_ms": BASE, "end_ms": BASE + 90 * DAY}
SIGNAL = {
    "lower": 100.0,
    "middle": 101.0,
    "close": 101.0,
    "ratio": 0.5,
    "vol": 0.001,
    "valid": True,
    "filter_ok": True,
}


def quote(db, decision=BASE, now=None):
    q = core.make_quote(
        "BTCUSDT", decision, SIGNAL, decision + 2000 if now is None else now, RULES, MANIFEST, True
    )
    core.save_quote(db, q)
    return q


def trade(identity, ts, price, maker=True, qty="5", **extra):
    return {"a": identity, "T": ts, "p": str(price), "q": qty, "m": maker, **extra}


def test_features_equal_original_and_restart_replay():
    from nautilus_trader.model import BarType

    config = SimpleNamespace(
        ma_filter_window=54,
        bar_type=BarType.from_str("BTCUSDT.BINANCE-1-MINUTE-LAST-EXTERNAL"),
        bollinger_period=20,
        bollinger_std_multiplier=2.0,
        ma_filter_period=99,
        recovery_ma_period=52,
        recovery_snapshot_bars=26,
    )
    original = EthFeatureModel(config)
    agg5, agg15 = CompletedBarAggregator(5), CompletedBarAggregator(15)
    state, replay = core.SignalState(), core.SignalState()
    signals = 0
    for i in range(1, 3101):
        end = BASE + i * MINUTE
        price = 100 + 0.001 * i + 2 * math.sin(i / 30)
        prices = (price, price + 0.02, price - 0.02, price)
        ts = (end - 1) * 1_000_000
        b5, b15 = agg5.update(ts, *prices), agg15.update(ts, *prices)
        got = state.update(end, *prices)
        assert replay.update(end, *prices) == got
        if b5:
            bands = original.on_five_minute_bar(b5)
            if bands:
                assert got["lower"] == bands[0]
                assert got["middle"] == bands[1]
                assert got["ratio"] == original.adverse_ma99_ratio(None)
                signals += 1
        if b15:
            original.on_fifteen_minute_bar(b15)
    assert signals > 500 and got["ratio"] is not None and got["vol"] > 0
    state.update(end + 2 * MINUTE, *prices)
    assert len(state.adverse) == 0  # Missing minute forces full fresh warmup.


def test_quote_cannot_be_backdated_or_sized_up(tmp_path):
    db = core.connect(tmp_path)
    q = quote(db)
    assert q["prospective"] == 1 and q["active_ms"] == BASE + 3000
    assert Decimal(q["quantity"]) == Decimal("2.000")
    late = quote(db, now=BASE + 15_001)
    assert late["status"] == "missed_late"
    assert db.execute("SELECT recorded_ms FROM quotes").fetchone()[0] == BASE + 2000
    blocked = core.make_quote(
        "BTCUSDT", BASE, SIGNAL, BASE + 2000, {**RULES, "min_notional": "201"}, MANIFEST, True
    )
    assert blocked["status"] == "order_constraint" and blocked["quantity"] == q["quantity"]
    assert (
        core.make_quote("BTCUSDT", BASE - 5 * MINUTE, SIGNAL, BASE, RULES, MANIFEST, True)[
            "prospective"
        ]
        == 0
    )
    db.close()


def test_trade_activation_strict_volume_and_restart_idempotence(tmp_path):
    db = core.connect(tmp_path)
    with db:
        quote(db)
        core.observe_trades(
            db,
            "BTCUSDT",
            [
                trade(10, BASE + 2999, 99),  # Before actual activation: cannot fill.
                trade(11, BASE + 3000, 101),  # Quote armed above limit.
                trade(12, BASE + 4000, 100),  # Touch without penetration.
                trade(13, BASE + 5000, 99.9, maker=False),  # Buyer initiated.
                trade(14, BASE + 6000, 99.9, nq="1"),  # Ordinary volume too small.
            ],
        )
    assert [(r[0], r[1]) for r in db.execute("SELECT kind,trade_id FROM touches")] == [
        ("broad", 12)
    ]
    db.close()
    db = core.connect(tmp_path)
    final = trade(15, BASE + 7000, 99.9, nq="2.0")
    with db:
        core.observe_trades(db, "BTCUSDT", [final, final])
        core.observe_trades(db, "BTCUSDT", [final])
    assert db.execute("SELECT COUNT(*) FROM touches").fetchone()[0] == 2
    assert db.execute("SELECT trade_id FROM touches WHERE kind='strict'").fetchone()[0] == 15
    assert core.meta(db, "BTCUSDT:trade_id") == 15
    db.close()


def test_gap_invalidates_existing_touch_and_first_below_is_uncertain(tmp_path):
    db = core.connect(tmp_path)
    with db:
        quote(db)
        core.observe_trades(
            db, "BTCUSDT", [trade(1, BASE + 3000, 101), trade(2, BASE + 4000, 99.9)]
        )
        core.observe_trades(db, "BTCUSDT", [trade(4, BASE + 5000, 102)])
        quote(db, BASE + 5 * MINUTE)
        core.observe_trades(db, "BTCUSDT", [trade(5, BASE + 5 * MINUTE + 4000, 99)])
    first, second = db.execute("SELECT * FROM quotes ORDER BY decision_ms").fetchall()
    assert first["gapped"] == 1
    assert second["armed"] == -1 and second["status"] == "marketable_uncertain"
    assert (
        db.execute("SELECT COUNT(*) FROM touches WHERE decision_ms>?", (BASE,)).fetchone()[0] == 0
    )
    assert db.execute("SELECT COUNT(*) FROM incidents").fetchone()[0] == 1
    db.close()


def seed_outcomes(db, missing=False):
    quote(db)
    core.observe_trades(db, "BTCUSDT", [trade(1, BASE + 3000, 101), trade(2, BASE + 4000, 99.9)])
    anchor = BASE + MINUTE
    for i in range(1441):
        if missing and i == 20:
            continue
        # The touch minute's extreme must not pollute future MAE/MFE.
        price = 101 + i / 60
        hi, low = (1000, 1) if i == 0 else (price + 0.1, price - 0.1)
        db.execute(
            "INSERT INTO bars VALUES(?,?,?,?,?,?,?)",
            ("BTCUSDT", anchor + i * MINUTE, price, hi, low, price, anchor + i * MINUTE + 2000),
        )
    db.execute(
        "INSERT INTO funding VALUES(?,?,?,?,?)", ("BTCUSDT", BASE + 4000, "Regular", "0.5", "100")
    )  # Exactly at entry excluded.
    db.execute(
        "INSERT INTO funding VALUES(?,?,?,?,?)",
        ("BTCUSDT", anchor + HOUR, "Regular", "0.001", "110"),
    )  # Exit included.
    core.set_meta(db, "BTCUSDT:funding_covered_ms", anchor + 24 * HOUR)
    return anchor


def test_settlement_funding_costs_and_no_future_leak(tmp_path):
    db = core.connect(tmp_path)
    with db:
        anchor = seed_outcomes(db)
        core.settle(db, "BTCUSDT", anchor + HOUR)
    assert db.execute("SELECT COUNT(*) FROM outcomes").fetchone()[0] == 0
    with db:
        core.settle(db, "BTCUSDT", anchor + HOUR + MINUTE)
    r = db.execute("SELECT * FROM outcomes WHERE kind='strict' AND horizon=1").fetchone()
    assert r["gross_bp"] == pytest.approx(200)
    assert r["intraminute_bp"] == pytest.approx(100)
    assert r["funding_bp"] == pytest.approx(11)
    assert r["fee_bp"] == pytest.approx(7.1)
    assert r["net_bp"] == pytest.approx(181.9)
    assert r["stress_bp"] == pytest.approx(171.8)
    assert r["mae_bp"] == 0 and r["mfe_bp"] == pytest.approx(210)
    with db:
        core.settle(db, "BTCUSDT", anchor + 24 * HOUR + MINUTE)
        core.settle(db, "BTCUSDT", anchor + 24 * HOUR + MINUTE)
    assert db.execute("SELECT COUNT(*) FROM outcomes").fetchone()[0] == 6
    # A late funding publication is reconciled by final analysis.
    with db:
        db.execute(
            "INSERT INTO funding VALUES(?,?,?,?,?)",
            ("BTCUSDT", BASE + 30 * MINUTE, "Special", "0.0001", "100"),
        )
    events, _ = review.common_frame(db, "BTCUSDT", MANIFEST)
    assert events.iloc[0].funding_revision_bp_1 == pytest.approx(1)
    assert events.iloc[0].stress_bp_1 == pytest.approx(170.8)
    db.close()


def test_missing_future_bar_never_assumed_flat(tmp_path):
    db = core.connect(tmp_path)
    with db:
        anchor = seed_outcomes(db, missing=True)
        core.settle(db, "BTCUSDT", anchor + 24 * HOUR + MINUTE)
    assert {r[0] for r in db.execute("SELECT status FROM outcomes")} == {"missing_bars"}
    events, _ = review.common_frame(db, "BTCUSDT", MANIFEST)
    assert not events.complete.any()
    db.close()


def test_public_only_api_and_closed_bar_revision_checks(tmp_path):
    db = core.connect(tmp_path)
    api = service.PublicAPI(tmp_path, db)
    for endpoint in ("order", "account", "listenKey", "../v2/account"):
        with pytest.raises(ValueError, match="public GET"):
            api.get(endpoint)
    with pytest.raises(ValueError, match="symbol"):
        api.get("aggTrades", symbol="BNBUSDT")
    k = [BASE, "100", "102", "99", "101", "5", BASE + MINUTE - 1]
    assert service.store_bars(db, "BTCUSDT", [k], BASE + MINUTE + 999) == []
    assert service.store_bars(db, "BTCUSDT", [k], BASE + MINUTE + 2000, BASE + MINUTE + 999) == []
    assert len(service.store_bars(db, "BTCUSDT", [k], BASE + MINUTE + 1000)) == 1
    assert service.store_bars(db, "BTCUSDT", [k], BASE + 2 * MINUTE) == []
    revised = list(k)
    revised[4] = "100.9"
    with pytest.raises(RuntimeError, match="revised"):
        service.store_bars(db, "BTCUSDT", [revised], BASE + 2 * MINUTE)
    db.close()


def test_spacing_precedes_future_availability_and_matching_is_frozen():
    frame = review.calendar(
        pd.DataFrame(
            {
                "ts": [BASE, BASE + 30 * MINUTE, BASE + HOUR],
                "vol": [0.01] * 3,
                "complete": [False, True, True],
            }
        )
    )
    selected = review.spaced(frame, 1)
    assert selected.index.tolist() == [0, 2]
    assert selected[selected.complete].index.tolist() == [2]
    pool = review.calendar(
        pd.DataFrame(
            {"ts": [BASE + 10 * MINUTE, BASE + 2 * DAY, BASE + 3 * DAY], "vol": [0.01, 0.01, 1.0]}
        )
    )
    result = review.matched(frame.iloc[:1], pool, 20260921)
    assert result[0].tolist() == [1] * 20  # Excludes near time and wrong past volatility.
    lo, hi = review.bounds([BASE + i * DAY for i in range(90)], np.ones(90), BASE, BASE + 90 * DAY)
    assert lo == hi == 1


def test_review_waits_for_date_and_insufficient_data_fails(tmp_path, monkeypatch):
    manifest = {**MANIFEST, "review_ms": BASE + 90 * DAY + 25 * HOUR}
    monkeypatch.setattr(review, "verify", lambda root: manifest)
    monkeypatch.setattr(review.time, "time", lambda: (manifest["review_ms"] - 1) / 1000)
    result = review.evaluate(tmp_path)
    assert result["state"] == "pending" and not (tmp_path / "assessment").exists()
    monkeypatch.setattr(review.time, "time", lambda: manifest["review_ms"] / 1000)
    result = review.evaluate(tmp_path)
    assert not result["research_signal_supported"]
    assert not result["quality_gate_passed"] and not result["live_trading_authorized"]
    assert (tmp_path / "review.html").exists()


def test_review_requires_both_markets_two_horizons_and_quality():
    counts = [
        {
            "symbol": s,
            "spacing_hours": 1,
            "common_events": 300,
            "active_days": 60,
            "quote_coverage": 0.95,
            "match_fraction": 0.9,
        }
        for s in core.SYMBOLS
    ]
    stats = [
        {
            "symbol": s,
            "spacing_hours": spacing,
            "horizon_hours": h,
            "statistic": k,
            "lower": 1,
            "mean": 2,
        }
        for s in core.SYMBOLS
        for spacing in (1, 24)
        for h in (1, 4, 24)
        for k in ("stress_net", "market_edge", "touch_edge")
    ]
    assert review.decide(stats, counts)["research_signal_supported"]
    assert not review.decide(stats, counts[:1])["research_signal_supported"]
    for r in stats:
        if r["horizon_hours"] in (4, 24) and r["symbol"] == "ETHUSDT":
            r["lower"] = -1
    assert not review.decide(stats, counts)["research_signal_supported"]


def test_freeze_detects_source_and_environment_changes(tmp_path, monkeypatch):
    source = tmp_path / "observer.py"
    source.write_text("frozen")
    manifest = {
        "parameters": core.PARAMETERS,
        "source_sha256": {source.name: core.sha(source)},
        "environment": {"python": core.sys.version, "packages": {}},
    }
    core.write_json(tmp_path / "manifest.json", manifest)
    (tmp_path / "frozen_sources.zip").write_bytes(b"archive fixture")
    core.write_json(
        tmp_path / "archive.json",
        {
            "manifest_sha256": core.sha(tmp_path / "manifest.json"),
            "sha256": core.sha(tmp_path / "frozen_sources.zip"),
        },
    )
    monkeypatch.setattr(core, "PROJECT", tmp_path)
    monkeypatch.setattr(core, "source_files", lambda: [source])
    assert core.verify(tmp_path) == manifest
    source.write_text("modified")
    with pytest.raises(RuntimeError, match="source changed"):
        core.verify(tmp_path)
    source.write_text("frozen")
    manifest["environment"]["python"] = "another version"
    core.write_json(tmp_path / "manifest.json", manifest)
    core.write_json(
        tmp_path / "archive.json",
        {
            "manifest_sha256": core.sha(tmp_path / "manifest.json"),
            "sha256": core.sha(tmp_path / "frozen_sources.zip"),
        },
    )
    with pytest.raises(RuntimeError, match="environment changed"):
        core.verify(tmp_path)


def test_raw_archive_chain_has_exact_durable_evidence(tmp_path):
    import gzip
    import hashlib

    db = core.connect(tmp_path)
    for n in range(2):
        core.archive_response(
            tmp_path, db, "aggTrades", {"symbol": "BTCUSDT"}, [trade(n, BASE, 100)], BASE
        )
    records = gzip.decompress(next((tmp_path / "raw").glob("*.gz")).read_bytes()).splitlines()
    chain = ""
    for record, row in zip(records, db.execute("SELECT * FROM requests ORDER BY id"), strict=True):
        digest = hashlib.sha256(record).hexdigest()
        chain = hashlib.sha256((chain + digest).encode()).hexdigest()
        assert row["payload_sha256"] == digest and row["chain_sha256"] == chain
    assert core.meta(db, "request_chain") == chain
    assert json.loads(records[0])["endpoint"] == "aggTrades"
    db.close()
