"""Raw identity, completeness and managed-journal recovery invariants."""

from datetime import UTC, datetime

import pytest

from core.market.instruments import bar_window
from core.market.models import DataError
from feed.pair_store import PairStore, journal_inventory, journal_preview


def trade(identity, stamp, price="10"):
    return {"id": identity, "time": stamp, "price": price, "qty": "2", "isBuyerMaker": False}


def test_raw_identity_deduplicates_nonconsecutive_ids_and_preserves_conflicts(tmp_path):
    path = tmp_path / "collection.sqlite"
    store = PairStore(path, "ETHUSDT", "tick")
    store.ticks([trade(1, 0), trade(3, 1000)], 1500)
    assert not store.db.execute("SELECT first_id,last_id FROM gaps").fetchall()
    store.ticks([trade(2, 500), trade(3, 1000, "10.00")], 1500)
    assert not store.db.execute("SELECT first_id,last_id FROM gaps").fetchall()
    assert store.db.execute("SELECT count(*) FROM trades").fetchone()[0] == 3
    with pytest.raises(DataError, match="Conflicting"):
        store.ticks([trade(3, 1000, "11")], 1500)
    assert store.conflicted
    store.close()
    resumed = PairStore(path, "ETHUSDT", "tick")
    assert resumed.last_id == 3 and resumed.conflicted
    assert journal_inventory(path, "ETHUSDT")[0]["level"] == "tick"
    assert [row["trade_id"] for row in journal_preview(path, "tick", 0, 2000, 10)] == ["1", "2", "3"]
    resumed.close()


def test_seconds_are_derived_once_and_closed_conflicts_preserve_original(tmp_path):
    path = tmp_path / "collection.sqlite"
    store = PairStore(path, "ETHUSDT", "1s")
    store.ticks([trade(1, 100)], 500)
    assert not store.latest["1s"]["is_closed"]
    store.ticks([trade(1, 100), trade(2, 1100)], 1500)
    assert store.db.execute("SELECT count(*) FROM candles").fetchone()[0] == 1
    store.ticks([trade(2, 1100), trade(3, 1500)], 2500)
    value = store.latest["1s"]
    assert value["volume"] == "4" and value["is_closed"] and value["is_complete"]
    assert not value["allow_new_risk"]
    store.ticks([trade(2, 1100), trade(3, 1500)], 2500)
    assert store.latest["1s"]["volume"] == "4"
    assert {item["level"] for item in journal_inventory(path, "ETHUSDT")} == {"1s"}
    assert journal_preview(path, "tick", 0, 3000, 10) == []
    with pytest.raises(DataError, match="Conflicting closed"):
        store.candle("1s", [1000, "12", "12", "12", "12", "4", 1999], 2500)
    assert store.conflicted
    store.close()


def test_calendar_windows_and_authoritative_candles(tmp_path):
    def ms(value):
        return int(datetime.fromisoformat(value).replace(tzinfo=UTC).timestamp() * 1000)
    assert bar_window("1M", ms("2024-02-28")) == (ms("2024-02-01"), ms("2024-03-01"))
    assert bar_window("1M", ms("2026-12-31")) == (ms("2026-12-01"), ms("2027-01-01"))
    assert bar_window("1w", ms("2026-10-29")) == (ms("2026-10-26"), ms("2026-11-02"))
    # Actual /fapi/v1/klines 3d opens; the venue is one UTC day off the Unix epoch anchor.
    assert bar_window("3d", ms("2026-10-28")) == (ms("2026-10-26"), ms("2026-10-29"))
    assert bar_window("3d", ms("2026-10-29")) == (ms("2026-10-29"), ms("2026-11-01"))
    store = PairStore(tmp_path / "collection.sqlite", "ETHUSDT", "1M")
    left, right = bar_window("1M", ms("2024-02-28"))
    row = [left, "10", "12", "9", "11", "2", right - 1]
    assert not store.candle("1M", row, right - 100)["is_closed"]
    assert store.candle("1M", row, right)["is_complete"]
    assert store.db.execute("SELECT count(*) FROM candles").fetchone()[0] == 1
    with pytest.raises(DataError, match="boundary"):
        store.candle("1M", [left, "10", "12", "9", "11", "2", left + 30 * 86400000 - 1], right)
    store.close()


def test_collection_ignores_legacy_id_gaps_and_never_polls_derived_periods(tmp_path, monkeypatch):
    import asyncio

    import feed.pair_service as module
    path = tmp_path / "collection.sqlite"
    store = PairStore(path, "ETHUSDT", "tick", ["1m"])
    store.ticks([trade(1, 170000)], 170500)
    with store.db:
        store.db.execute("INSERT INTO gaps VALUES (2,2,170000,179001)")
    store.close()
    requests, states = [], []

    class Clock:
        base_ms = 180001
        healthy = True
        async def sync(self, rest):
            pass
        def now(self):
            return 180001
        def confirmed(self):
            return 180000

    class Rest:
        def __init__(self, **kwargs):
            pass
        async def get(self, endpoint, **params):
            requests.append(endpoint)
            assert endpoint == "trades"
            return [trade(3, 179000)]

    class Socket:
        async def stream(self, symbol):
            yield {"kind": "connected"}
            yield {"kind": "trade", "row": trade(3, 179000)}
            await asyncio.Event().wait()

    def status(root, value):
        states.append(value)
        if value["state"] != "STOPPED":
            raise asyncio.CancelledError

    monkeypatch.setattr(module, "PairREST", Rest)
    monkeypatch.setattr(module, "PairTradeSocket", Socket)
    monkeypatch.setattr(module, "ServerClock", Clock)
    monkeypatch.setattr(module, "write_status", status)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(module.serve_pair({"symbol": "ETHUSDT", "base_level": "tick", "intervals": ["1m"]}, tmp_path, tmp_path / "catalog"))
    assert requests == ["trades"]
    assert states[0]["state"] == "READY"
    assert states[0]["data_ready"] and not states[0]["allow_new_risk"]
    assert states[0]["gap_count"] == 0
    assert states[-1]["state"] == "STOPPED"
    resumed = PairStore(path, "ETHUSDT", "tick", ["1m"])
    assert resumed.db.execute("SELECT first_id,last_id FROM gaps").fetchall() == [(2, 2)]
    import json
    candle = json.loads(resumed.db.execute("SELECT value FROM candles WHERE interval='1m' AND start_ms=120000").fetchone()[0])
    assert not candle["is_complete"]  # The initial partial minute remains incomplete, regardless of old ID gaps.
    assert candle["volume"] == "4"
    assert not resumed.db.execute("SELECT 1 FROM candles WHERE interval='1s'").fetchone()
    resumed.close()


def test_conflicting_duplicate_ids_in_one_page_never_silently_choose_one(tmp_path):
    store = PairStore(tmp_path / "collection.sqlite", "ETHUSDT", "tick")
    with pytest.raises(DataError, match="Conflicting raw"):
        store.ticks([trade(1, 100, "10"), trade(1, 100, "11")], 200)
    assert store.conflicted
    assert not store.db.execute("SELECT * FROM trades").fetchall()
    store.close()


def test_trade_changes_recompute_only_affected_bars_and_duplicate_is_a_noop(tmp_path):
    store = PairStore(tmp_path / "collection.sqlite", "ETHUSDT", "tick", ["1s", "1m", "5m"])
    store.ticks([trade(1, 100, "10"), trade(2, 1100, "12")], 1500)
    assert store.latest["1m"]["volume"] == "4"
    assert store.latest["5m"]["high"] == "12"
    revision = store.revision
    store.ticks([trade(1, 100, "10"), trade(2, 1100, "12")], 1500)
    assert store.revision == revision
    store.ticks([trade(3, 1500, "9")], 2500)
    assert store.latest["1m"]["volume"] == "6"
    assert store.latest["1m"]["open"] == "10"
    assert store.latest["1m"]["close"] == "9"
    assert store.latest["5m"]["low"] == "9"
    assert not store.latest["5m"]["is_complete"]
    assert not store.db.execute("SELECT * FROM pending_updates").fetchall()
    store.close()


def test_out_of_order_and_late_change_recompute_derived_closes(tmp_path):
    store = PairStore(tmp_path / "collection.sqlite", "ETHUSDT", "tick", ["1s", "1m"])
    store.ticks([trade(3, 1100)], 1500)
    store.ticks([trade(1, 100)], 1600)
    assert not store.db.execute("SELECT first_id,last_id FROM gaps").fetchall()
    store.ticks([trade(2, 500)], 1700)
    assert not store.db.execute("SELECT * FROM gaps").fetchall()
    assert store.latest["1m"]["volume"] == "6"
    store.ticks([trade(4, 2100)], 2500)
    before = store.db.execute("SELECT value FROM candles WHERE interval='1s' AND start_ms=1000").fetchone()[0]
    # Later source evidence changes the affected bucket; it must not add a second copy.
    store.ticks([trade(5, 1700, "11")], 2600)
    after = store.db.execute("SELECT value FROM candles WHERE interval='1s' AND start_ms=1000").fetchone()[0]
    import json
    assert json.loads(before)["volume"] == "2"
    assert json.loads(after)["volume"] == "4"
    assert store.latest["1m"]["volume"] == "10"
    store.close()


def test_dirty_updates_recover_after_interrupted_aggregation(tmp_path, monkeypatch):
    path = tmp_path / "collection.sqlite"
    store = PairStore(path, "ETHUSDT", "tick", ["1m"])
    def fail(*args, **kwargs):
        raise RuntimeError("interrupted before minute write")
    monkeypatch.setattr(store, "candle", fail)
    with pytest.raises(RuntimeError, match="interrupted"):
        store.ticks([trade(1, 100), trade(2, 1100)], 2500)
    assert store.db.execute("SELECT count(*) FROM trades").fetchone()[0] == 2
    assert store.db.execute("SELECT count(*) FROM pending_updates").fetchone()[0] > 0
    store.close()
    resumed = PairStore(path, "ETHUSDT", "tick", ["1m"])
    resumed.process_changes(2500)
    assert resumed.latest["1m"]["volume"] == "4"
    assert not resumed.db.execute("SELECT * FROM pending_updates").fetchall()
    resumed.close()


def test_internal_inputs_are_not_retained_datasets_or_public_updates(tmp_path):
    path = tmp_path / "collection.sqlite"
    store = PairStore(path, "ETHUSDT", "1m", ["5m"])
    store.ticks([trade(1, 100), trade(2, 1100)], 61000)
    assert "1s" not in store.latest
    assert not store.db.execute("SELECT 1 FROM candles WHERE interval='1s'").fetchone()
    assert {value["level"] for value in journal_inventory(path, "ETHUSDT")} == {"1m"}
    assert journal_preview(path, "1s", 0, 60000, 100) == []
    assert journal_preview(path, "tick", 0, 60000, 100) == []
    store.close()


def test_forming_revision_survives_restart_and_later_windows_do_not_rewrite_old_buckets(tmp_path):
    path = tmp_path / "collection.sqlite"
    store = PairStore(path, "ETHUSDT", "tick", ["1m", "5m"])
    store.ticks([trade(1, 100)], 500)
    revision = store.revision
    store.close()
    resumed = PairStore(path, "ETHUSDT", "tick", ["1m", "5m"])
    resumed.ticks([trade(2, 1100)], 1500)
    assert resumed.revision > revision
    resumed.ticks([trade(3, 60100)], 61000)
    before = resumed.db.execute("SELECT value FROM candles WHERE interval='1m' AND start_ms=0").fetchone()[0]
    resumed.ticks([trade(4, 61100)], 62000)
    assert resumed.db.execute("SELECT value FROM candles WHERE interval='1m' AND start_ms=0").fetchone()[0] == before
    resumed.close()


@pytest.mark.parametrize("snapshot_fails", [False, True])
@pytest.mark.parametrize("zero_event", [False, True])
def test_live_events_continue_during_handover_failure_and_shutdown_closes_socket(tmp_path, monkeypatch, snapshot_fails, zero_event):
    import asyncio

    import feed.pair_service as module
    async def scenario():
        gate = asyncio.Event()
        closed = []
        states, requests = [], []
        if snapshot_fails:
            previous = PairStore(tmp_path / "collection.sqlite", "ETHUSDT", "tick", ["1m"])
            with previous.db:
                previous.db.execute("INSERT INTO conflicts VALUES ('existing conflict')")
            previous.close()
        class Clock:
            base_ms = 2001
            healthy = True
            async def sync(self, rest):
                pass
            def now(self):
                return 2001
            def confirmed(self):
                return 2000
        class Rest:
            def __init__(self, **kwargs):
                pass
            async def get(self, endpoint, **kwargs):
                requests.append(endpoint)
                if snapshot_fails:
                    raise DataError("Public REST retries exhausted")
                return [trade(1, 1100)]
        class Socket:
            async def stream(self, symbol):
                try:
                    yield {"kind": "connected"}
                    yield {"kind": "trade", "row": trade(1, 1100)}
                    if zero_event:
                        yield {"kind": "trade", "row": trade(2, 1300, "0")}
                    await gate.wait()
                    yield {"kind": "trade", "row": trade(3 if zero_event else 2, 1500)}
                    await asyncio.Event().wait()
                finally:
                    closed.append(True)
        def publish(root, value):
            if value["state"] == "STOPPED":
                return
            states.append(value)
            if len(states) == 1:
                gate.set()
            else:
                raise asyncio.CancelledError
        monkeypatch.setattr(module, "PairREST", Rest)
        monkeypatch.setattr(module, "PairTradeSocket", Socket)
        monkeypatch.setattr(module, "ServerClock", Clock)
        monkeypatch.setattr(module, "write_status", publish)
        with pytest.raises(asyncio.CancelledError):
            await module.serve_pair({"symbol": "ETHUSDT", "base_level": "tick", "intervals": ["1m"]}, tmp_path, tmp_path / "catalog")
        assert closed and requests == ["trades"]
        assert states[0]["state"] == ("DEGRADED" if snapshot_fails else "READY")
        assert states[-1]["state"] == ("DEGRADED" if snapshot_fails else "READY")
        assert states[-1]["gap_count"] == 0
        assert states[-1]["bar_updates"][0]["volume"] == "4"
        assert all(not value["allow_new_risk"] for value in states)
        store = PairStore(tmp_path / "collection.sqlite", "ETHUSDT", "tick", ["1m"])
        assert store.db.execute("SELECT count(*) FROM trades").fetchone()[0] == 2
        store.close()
    asyncio.run(scenario())


def test_complete_minute_requires_all_source_seconds_and_late_data_recomputes_it(tmp_path):
    import json
    store = PairStore(tmp_path / "collection.sqlite", "ETHUSDT", "tick", ["1m", "5m"])
    store.ticks([trade(1, 59100)], 59500)
    rows = [trade(index + 2, 60100 + index * 1000, str(10 + index)) for index in range(60)]
    missing = rows[20]
    store.ticks([row for row in rows if row != missing], 120500)
    before = json.loads(store.db.execute("SELECT value FROM candles WHERE interval='1m' AND start_ms=60000").fetchone()[0])
    assert before["is_closed"] and not before["is_complete"]
    store.ticks([missing], 121000)
    after = json.loads(store.db.execute("SELECT value FROM candles WHERE interval='1m' AND start_ms=60000").fetchone()[0])
    assert after["is_complete"] and after["volume"] == "120"
    assert after["open"] == "10" and after["close"] == "69"
    revision = store.revision
    store.process_changes(600000)
    assert store.revision == revision  # Time alone does not create or close candles.
    store.close()


def test_removing_a_period_keeps_previously_retained_candles(tmp_path):
    path = tmp_path / "collection.sqlite"
    store = PairStore(path, "ETHUSDT", "tick", ["1s"])
    store.ticks([trade(1, 100), trade(2, 1100)], 2500)
    store.close()
    resumed = PairStore(path, "ETHUSDT", "tick", ["1m"])
    resumed.ticks([trade(3, 181100)], 182000)
    assert resumed.db.execute("SELECT count(*) FROM candles WHERE interval='1s' AND start_ms=0").fetchone()[0] == 1
    assert "1s" in {value["level"] for value in journal_inventory(path, "ETHUSDT")}
    assert "1s" not in resumed.latest
    resumed.close()


@pytest.mark.parametrize("price,size", [("0", "0"), ("0", "2"), ("10", "0")])
def test_live_zero_records_are_filtered_without_conflicts_or_false_gaps(tmp_path, price, size):
    path = tmp_path / "collection.sqlite"
    store = PairStore(path, "ETHUSDT", "tick", ["1s", "1m"])
    invalid = {**trade(2, 1100, price), "qty": size}
    with pytest.raises(DataError, match="Invalid raw trade"):
        store.ticks([invalid], 2000)
    result = store.ticks([trade(1, 1000), invalid, trade(3, 1200)], 2000, quarantine_invalid=True)
    assert [row[0] for row in result] == [1, 3]
    assert store.db.execute("SELECT id FROM trades ORDER BY id").fetchall() == [(1,), (3,)]
    assert not store.conflicted
    assert not store.db.execute("SELECT * FROM gaps").fetchall()
    assert store.latest["1m"]["volume"] == "4"
    assert store.latest["1m"]["low"] == "10"
    store.close()
    resumed = PairStore(path, "ETHUSDT", "tick", ["1m"])
    assert not resumed.conflicted
    resumed.ticks([trade(4, 1300)], 2000)
    assert not resumed.db.execute("SELECT * FROM gaps").fetchall()
    assert resumed.db.execute("SELECT count(*) FROM conflicts").fetchone()[0] == 1
    resumed.close()


def test_legacy_zero_evidence_is_filtered_and_preserves_other_conflicts(tmp_path):
    import json

    path = tmp_path / "collection.sqlite"
    store = PairStore(path, "ETHUSDT", "tick")
    store.ticks([trade(1, 1000), trade(4, 1300)], 2000)
    evidence = json.dumps({"kind": "invalid_raw_trade", "row": trade(2, 1100, "0")})
    with store.db:
        store.db.execute("INSERT INTO conflicts VALUES (?)", (evidence,))
    store.close()
    resumed = PairStore(path, "ETHUSDT", "tick")
    resumed.ticks([], 2000)
    assert not resumed.conflicted
    assert not resumed.db.execute("SELECT first_id,last_id FROM gaps").fetchall()
    resumed.ticks([trade(3, 1200)], 2000)
    assert not resumed.db.execute("SELECT * FROM gaps").fetchall()
    assert resumed.db.execute("SELECT detail FROM conflicts").fetchall() == [(evidence,)]
    # An eventual valid record remains a real Tick, independent of the filter receipt.
    resumed.ticks([trade(2, 1100)], 2000)
    with resumed.db:
        resumed.db.execute("INSERT INTO conflicts VALUES (?)", (json.dumps({"kind": "invalid_raw_trade", "row": trade(5, 1500, "-1")}),))
    assert resumed.conflicted
    resumed.close()


@pytest.mark.parametrize("price,size", [("-1", "2"), ("10", "-2"), ("0", "-2"), ("NaN", "0"), ("Infinity", "0")])
def test_live_negative_and_nonfinite_values_remain_errors(tmp_path, price, size):
    store = PairStore(tmp_path / "collection.sqlite", "ETHUSDT", "tick")
    with pytest.raises(DataError, match="Invalid raw trade"):
        store.ticks([{**trade(1, 1000, price), "qty": size}], 2000, quarantine_invalid=True)
    assert not store.db.execute("SELECT * FROM trades").fetchall()
    store.close()


def test_collection_can_commit_without_calculation_and_consumer_recovers_pending(tmp_path):
    path = tmp_path / "collection.sqlite"
    collector = PairStore(path, "ETHUSDT", "1s")
    collector.ticks([trade(1, 100)], 500, calculate=False)
    collector.ticks([trade(2, 1100)], 1500, calculate=False)
    assert collector.db.execute("SELECT count(*) FROM trades").fetchone()[0] == 2
    assert collector.db.execute("SELECT count(*) FROM candles").fetchone()[0] == 0
    collector.close()
    consumer = PairStore(path, "ETHUSDT", "1s")
    consumer.process_changes(1500)
    assert consumer.db.execute("SELECT count(*) FROM pending_updates").fetchone()[0] == 0
    assert consumer.db.execute("SELECT count(*) FROM candles").fetchone()[0] == 1
    original = consumer.db.execute("SELECT value FROM candles").fetchone()[0]
    consumer.close()
    restarted = PairStore(path, "ETHUSDT", "1s")
    restarted.process_changes(1500)
    assert restarted.db.execute("SELECT value FROM candles").fetchone()[0] == original
    restarted.close()


def test_history_change_is_not_acknowledged_on_failure_and_can_resume(tmp_path, monkeypatch):
    import json

    from feed.history import CatalogHistory
    change = tmp_path / "history/changes/2026-10-28.json"
    change.parent.mkdir(parents=True)
    event = {"state": "ready", "end": "2026-10-29T00:00:00+00:00"}
    change.write_text(json.dumps(event))
    def failed(*_args, **_kwargs):
        raise ValueError("incomplete source")
    monkeypatch.setattr("pipeline.pair_data.calculate_history_change", failed)
    with pytest.raises(ValueError, match="incomplete source"):
        CatalogHistory(tmp_path / "catalog").consume_change(change)
    assert json.loads(change.read_text()) == event
    calls = []
    monkeypatch.setattr("pipeline.pair_data.calculate_history_change", lambda *args, **kwargs: calls.append((args, kwargs)))
    CatalogHistory(tmp_path / "catalog").consume_change(change)
    assert len(calls) == 1 and not change.exists()


def test_slow_history_recalculation_does_not_block_realtime_dirty_buckets(tmp_path, monkeypatch):
    import asyncio
    import json
    import sqlite3
    import threading
    import time

    import feed.pair_calculation as module
    monkeypatch.setattr(module, 'PROJECT_ROOT', tmp_path)
    now = int(time.time() * 1000)
    left = now // 1000 * 1000 - 4000
    root = tmp_path / 'journal'
    root.mkdir()
    path = root / 'collection.sqlite'
    store = PairStore(path, 'ETHUSDT', '1s')
    store.ticks([trade(1, left + 100)], left + 500, calculate=False)
    store.close()
    status = root / 'status.json'
    status.write_text(json.dumps({'confirmed_ms': left + 500, 'valid_until_ms': now + 60000}))
    changes = root / 'history/changes'
    changes.mkdir(parents=True)
    (changes / 'day.json').write_text('{}')
    started, release = threading.Event(), threading.Event()
    def slow_history(_self, _path):
        started.set()
        assert release.wait(10)
        return True
    monkeypatch.setattr(module.CatalogHistory, 'consume_change', slow_history)
    async def scenario():
        task = asyncio.create_task(module.serve_calculation(
            {'symbol': 'ETHUSDT', 'product': 'futures_um', 'base_level': '1s', 'intervals': []}, root, tmp_path / 'catalog'))
        try:
            assert await asyncio.to_thread(started.wait, 3)
            producer = PairStore(path, 'ETHUSDT', '1s')
            producer.ticks([trade(2, left + 1100)], left + 1500, calculate=False)
            producer.close()
            status.write_text(json.dumps({'confirmed_ms': left + 2500, 'valid_until_ms': now + 60000}))
            deadline = time.monotonic() + 4
            found = False
            while time.monotonic() < deadline:
                with sqlite3.connect(path) as db:
                    found = bool(db.execute("SELECT 1 FROM candles WHERE interval='1s' AND start_ms=?", (left + 1000,)).fetchone())
                if found:
                    break
                await asyncio.sleep(.05)
            assert found, 'Live calculation waited behind historical work'
            assert not release.is_set()
        finally:
            release.set()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    asyncio.run(scenario())
