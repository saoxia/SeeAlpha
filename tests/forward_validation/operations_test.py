"""Operational recovery must preserve evidence and the locked scientific decision."""

import gzip
import hashlib
import json
import sqlite3
from pathlib import Path

import pandas as pd
import pytest

from forward_validation import core, review
from ops import forward_watchdog as watch
from ops import sample_quality as quality
from ops import study_backup as backup
from ops import study_review as guard

BASE = 20_000 * core.DAY


@pytest.fixture
def study(tmp_path, monkeypatch):
    root = tmp_path / "study"
    root.mkdir()
    manifest = {
        "start_ms": BASE,
        "end_ms": BASE + 90 * core.DAY,
        "review_ms": BASE + 90 * core.DAY + 25 * core.HOUR,
        "parameters": core.PARAMETERS,
        "rules": {"BTCUSDT": {}, "ETHUSDT": {}},
    }
    (root / "manifest.json").write_text(json.dumps(manifest))
    (root / "archive.json").write_text("{}")
    (root / "frozen_sources.zip").write_bytes(b"fixture archive")
    db = core.connect(root)
    core.archive_response(root, db, "time", {}, {"serverTime": BASE}, BASE)
    core.archive_response(root, db, "time", {}, {"serverTime": BASE + 1}, BASE + 1)
    db.close()
    for module in (quality, backup, guard, review):
        monkeypatch.setattr(module, "verify", lambda path: manifest)
    return root, manifest


def test_quality_does_not_read_returns_or_modify_database(study, monkeypatch):
    root, _ = study
    before = (root / "observations.sqlite").read_bytes()
    original = sqlite3.connect
    read_columns = set()

    def connect(*args, **kwargs):
        db = original(*args, **kwargs)

        def authorizer(action, table, column, *unused):
            if action == sqlite3.SQLITE_READ:
                read_columns.add(column)
            return sqlite3.SQLITE_OK

        db.set_authorizer(authorizer)
        return db

    monkeypatch.setattr(quality.sqlite3, "connect", connect)
    result = quality.snapshot(root, now_ms=BASE + core.HOUR)
    assert result["returns_examined"] is False
    assert not read_columns & {
        "gross_bp",
        "net_bp",
        "stress_bp",
        "intraminute_bp",
        "after_close_bp",
    }
    assert result["markets"][0]["samples"][0]["common_events"] == 0
    assert not result["markets"][0]["operations_week"]["full_week_observed"]
    assert (root / "observations.sqlite").read_bytes() == before


def test_quality_spacing_precedes_missingness_and_uses_both_matches():
    events = review.calendar(
        pd.DataFrame(
            {
                "decision_ms": [BASE, BASE + core.HOUR // 2, BASE + core.HOUR, BASE + 2 * core.DAY],
                "ts": [BASE, BASE + core.HOUR // 2, BASE + core.HOUR, BASE + 2 * core.DAY],
                "vol": [0.01] * 4,
                "filter_ok": [1, 1, 1, 0],
                "complete": [False, True, True, True],
            }
        )
    )
    controls = review.calendar(pd.DataFrame({"ts": [BASE + 3 * core.DAY], "vol": [0.01]}))
    main, sensitive = quality.summarize_samples(events, controls, core.PARAMETERS["seed"])
    assert main["eligible_touches"] == 3 and main["spaced_events"] == 2
    assert main["complete_events"] == main["common_events"] == main["active_days"] == 1
    assert sensitive["complete_events"] == 0  # First incomplete event still consumes spacing.


def test_quote_coverage_excludes_open_deadline_and_caps_at_end(study):
    root, manifest = study
    with sqlite3.connect(root / "observations.sqlite") as db:
        assert quality.decision_counts(db, "BTCUSDT", manifest, BASE, BASE - 1)["expected"] == 0
        assert quality.decision_counts(db, "BTCUSDT", manifest, BASE, BASE)["expected"] == 1
        full = quality.decision_counts(db, "BTCUSDT", manifest, BASE, manifest["review_ms"])
        assert full["expected"] == 25920


def test_backup_restores_consistent_database_and_detects_raw_corruption(study, tmp_path):
    root, _ = study
    result = backup.create(root, tmp_path / "backups")
    assert result["verified"] and result["restore_rehearsal"] == "passed"
    assert result["requests"] == 2
    directory = Path(result["directory"])
    record = json.loads((directory / "backup.json").read_text())
    raw = next(iter(record["raw"].values()))
    obj = directory.parent.parent / raw["object"]
    obj.write_bytes(b"corrupted")
    with pytest.raises(RuntimeError, match="raw object changed"):
        backup.validate_snapshot(directory)


def test_backup_keeps_committed_prefix_with_trailing_uncommitted_record(tmp_path):
    source, target = tmp_path / "raw.gz", tmp_path / "backup.gz"
    lines = [b'{"a":0}', b'{"a":1}', b'{"a":2}']
    with gzip.open(source, "wb") as file:
        file.write(b"\n".join(lines) + b"\n")
    skipped = backup.archive_prefix(source, target, [hashlib.sha256(lines[1]).hexdigest()])
    assert skipped == 1
    with gzip.open(target, "rb") as file:
        assert file.read() == lines[1] + b"\n"


def test_backup_rejects_missing_committed_evidence(study, tmp_path):
    root, _ = study
    for path in (root / "raw").iterdir():
        with gzip.open(path, "wb") as file:
            file.write(b"orphaned\n")
    with pytest.raises(RuntimeError, match="ends before committed"):
        backup.create(root, tmp_path / "backups")
    assert not (tmp_path / "backups/latest.json").exists()


def test_review_guard_does_nothing_before_fixed_deadline(study, monkeypatch):
    root, manifest = study
    monkeypatch.setattr(guard.time, "time", lambda: (manifest["review_ms"] - 1) / 1000)
    monkeypatch.setattr(guard.subprocess, "run", lambda *a, **k: pytest.fail("stopped early"))
    before = set(root.iterdir())
    assert guard.finalize(root)["state"] == "pending"
    assert set(root.iterdir()) == before


def test_due_offline_review_reports_insufficient_and_is_idempotent(study, monkeypatch):
    root, manifest = study
    monkeypatch.setattr(guard.time, "time", lambda: manifest["review_ms"] / 1000)
    original = (root / "observations.sqlite").read_bytes()
    result = guard.finalize(root, stop_service=False)
    assert result["state"] == "completed" and not result["live_trading_authorized"]
    assert "证据不足" in result["decision"]
    assert (root / "observations.sqlite").read_bytes() == original
    assert guard.finalize(root, stop_service=False) == result
    (root / "review.html").write_text("changed")
    with pytest.raises(RuntimeError, match="assessment changed"):
        guard.finalize(root, stop_service=False)


def test_notifications_debounce_deduplicate_and_report_recovery():
    problem = {"clock_unhealthy": "Clock check failed"}
    state, event = watch.transition({}, problem, BASE)
    assert event is None
    state, event = watch.transition(state, problem, BASE + 3 * core.MINUTE)
    assert event["state"] == "attention"
    state, event = watch.transition(state, problem, BASE + 4 * core.MINUTE)
    assert event is None
    state, event = watch.transition(state, {}, BASE + 5 * core.MINUTE)
    assert event is None
    state, event = watch.transition(state, {}, BASE + 7 * core.MINUTE)
    assert event["state"] == "recovered"


def test_changing_fault_codes_do_not_hide_a_continuous_outage():
    state, _ = watch.transition({}, {"clock": "clock"}, BASE)
    state, event = watch.transition(state, {"trade": "trade"}, BASE + 4 * core.MINUTE)
    assert event["state"] == "attention" and state["active_codes"] == ["trade"]


def test_windows_status_with_bom_can_be_read(tmp_path):
    path = tmp_path / "windows.json"
    path.write_text('{"keep_awake":true}', encoding="utf-8-sig")
    assert watch.read_json(path)["keep_awake"]


def test_watchdog_respects_manual_disable_freeze_deadline_and_restart_cooldown():
    health = {"problems": [{"code": "stale_heartbeat"}], "review_ms": BASE + core.DAY}
    state = {"first_bad_ms": BASE - 4 * core.MINUTE}
    assert watch.restart_allowed(health, state, BASE, True)
    assert not watch.restart_allowed(health, state, BASE, False)
    assert not watch.restart_allowed(health, state, BASE + core.DAY, True)
    state["last_restart_ms"] = BASE - core.MINUTE
    assert not watch.restart_allowed(health, state, BASE, True)
    state.pop("last_restart_ms")
    health["problems"].append({"code": "frozen_source_changed"})
    assert not watch.restart_allowed(health, state, BASE, True)
