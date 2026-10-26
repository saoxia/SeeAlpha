"""Recovery cannot skip even one already recorded prospective quote."""

import pytest

from forward_validation.core import connect, meta, set_meta
from ops.recover_forward_feed import apply_batches, plan_resync

NOW = 1_789_949_100_000


def trades(start=200):
    return [
        {"a": start, "T": NOW - 2000, "p": "100", "q": "5", "m": True},
        {"a": start + 1, "T": NOW - 1000, "p": "101", "q": "5", "m": False},
    ]


@pytest.fixture
def db(tmp_path):
    db = connect(tmp_path)
    with db:
        for symbol in ("BTCUSDT", "ETHUSDT"):
            set_meta(db, symbol + ":trade_id", 100)
            set_meta(db, symbol + ":trade_ts", NOW - 12 * 3_600_000)
    yield db
    db.close()


def add_quote(db, *, prospective=1):
    db.execute(
        """INSERT INTO quotes(symbol,decision_ms,active_ms,expires_ms,prospective,armed,gapped)
        VALUES('ETHUSDT',?,?,?, ?,0,0)""",
        (NOW - 5000, NOW - 4000, NOW + 1000, prospective),
    )
    db.commit()


def test_quote_free_gap_is_recorded_and_missed_signal_stays_missed(db):
    add_quote(db, prospective=0)
    result = apply_batches(db, {"BTCUSDT": trades(), "ETHUSDT": trades()}, NOW)
    assert all(p["skipped_trade_ids"] == 99 and p["protected_quotes"] == 0 for p in result)
    assert meta(db, "BTCUSDT:trade_id") == meta(db, "ETHUSDT:trade_id") == 201
    assert db.execute("SELECT prospective,gapped FROM quotes").fetchone()[:] == (0, 1)
    assert db.execute("SELECT COUNT(*) FROM touches").fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM incidents").fetchone()[0] == 4


def test_any_prospective_quote_blocks_both_markets_atomically(db):
    add_quote(db)
    with pytest.raises(ValueError, match="prospective quotes overlap"):
        apply_batches(db, {"BTCUSDT": trades(), "ETHUSDT": trades()}, NOW)
    assert meta(db, "BTCUSDT:trade_id") == meta(db, "ETHUSDT:trade_id") == 100
    assert db.execute("SELECT COUNT(*) FROM incidents").fetchone()[0] == 0
    assert db.execute("SELECT gapped FROM quotes").fetchone()[0] == 0


@pytest.mark.parametrize("problem", ["stale", "discontinuous", "future", "nan"])
def test_invalid_or_stale_replacement_is_rejected(db, problem):
    payload = trades()
    if problem == "stale":
        for trade in payload:
            trade["T"] -= 60_000
    elif problem == "discontinuous":
        payload[1]["a"] += 1
    elif problem == "future":
        payload[1]["T"] = NOW + 3000
    else:
        payload[0]["nq"] = "NaN"
    with pytest.raises(ValueError):
        plan_resync(db, "BTCUSDT", payload, NOW)
    assert meta(db, "BTCUSDT:trade_id") == 100
