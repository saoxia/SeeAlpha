"""Health is based on fresh market timestamps, never a reassuring stale label."""

import json
import sqlite3

import pytest

from ops import project_health as health

MINUTE = health.MINUTE
BASE = 1_789_949_100_000


@pytest.fixture
def study(tmp_path):
    root = tmp_path / "study"
    root.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    source = project / "frozen.py"
    source.write_text("frozen source")
    now = BASE + 30 * MINUTE + 30_000
    manifest = {
        "start_ms": BASE,
        "end_ms": BASE + 90 * 24 * health.HOUR,
        "review_ms": BASE + 91 * 24 * health.HOUR + health.HOUR,
        "rules": {"BTCUSDT": {}, "ETHUSDT": {}},
        "parameters": {"decision_deadline_ms": 15_000, "minimum_coverage": 0.95},
        "source_sha256": {"frozen.py": health.digest(source)},
    }
    (root / "manifest.json").write_text(json.dumps(manifest))
    (root / "frozen_sources.zip").write_text("frozen archive fixture")
    (root / "archive.json").write_text(
        json.dumps(
            {
                "manifest_sha256": health.digest(root / "manifest.json"),
                "sha256": health.digest(root / "frozen_sources.zip"),
            }
        )
    )
    status = {
        "updated_ms": now,
        "state": "持续观察中",
        "error": None,
        "rules_unchanged": True,
        "pid": 123,
        "clock": {"healthy": True, "server_ms": now, "host_ms": now},
    }
    (root / "status.json").write_text(json.dumps(status))
    with sqlite3.connect(root / "observations.sqlite") as db:
        db.executescript("""CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT);
            CREATE TABLE bars(symbol TEXT,end_ms INTEGER);
            CREATE TABLE quotes(symbol TEXT,decision_ms INTEGER,prospective INTEGER,armed INTEGER,gapped INTEGER,filter_ok INTEGER);
            CREATE TABLE touches(symbol TEXT,decision_ms INTEGER,kind TEXT);
            CREATE TABLE incidents(ts INTEGER,kind TEXT);""")
        for symbol in manifest["rules"]:
            for key, value in (
                ("trade_ts", now - 1000),
                ("trade_id", 42),
                ("funding_covered_ms", now - 15 * MINUTE),
            ):
                db.execute("INSERT INTO meta VALUES(?,?)", (symbol + ":" + key, json.dumps(value)))
            db.execute("INSERT INTO bars VALUES(?,?)", (symbol, BASE + 30 * MINUTE))
            for i in range(7):
                db.execute(
                    "INSERT INTO quotes VALUES(?,?,1,1,0,1)", (symbol, BASE + i * 5 * MINUTE)
                )
            db.execute("INSERT INTO touches VALUES(?,?,'strict')", (symbol, BASE))
    return root, project, now


def test_fresh_heartbeat_cannot_hide_twelve_hour_trade_backlog(study):
    root, project, now = study
    with sqlite3.connect(root / "observations.sqlite") as db:
        db.execute(
            "UPDATE meta SET value=? WHERE key LIKE '%:trade_ts'",
            (json.dumps(now - 12 * health.HOUR),),
        )
    result = health.snapshot(root, now_ms=now, project=project)
    assert result["state"] == "正在追赶或等待行情" and not result["healthy"]
    assert result["heartbeat_age_seconds"] == 0
    assert {p["code"] for p in result["problems"]} == {"trade_lag:BTCUSDT", "trade_lag:ETHUSDT"}
    assert all(r["trade_lag_seconds"] == 43200 for r in result["markets"])


def test_stale_stopped_and_changed_source_are_not_healthy(study):
    root, project, now = study
    status = health.read_json(root / "status.json")
    status.update(state="采集已停止", updated_ms=now - 6 * MINUTE)
    (root / "status.json").write_text(json.dumps(status))
    (project / "frozen.py").write_text("changed")
    result = health.snapshot(root, now_ms=now, project=project)
    codes = {p["code"] for p in result["problems"]}
    assert {"stopped", "stale_heartbeat", "frozen_source_changed"} <= codes
    assert result["state"] == "需要处理"


def test_counts_respect_open_deadline_and_invalidated_touches(study):
    root, project, now = study
    with sqlite3.connect(root / "observations.sqlite") as db:
        db.execute("UPDATE quotes SET gapped=1 WHERE symbol='BTCUSDT' AND decision_ms=?", (BASE,))
    result = health.snapshot(root, now_ms=now - 20_000, project=project)
    btc, eth = result["markets"]
    assert btc["expected_decisions"] == btc["prospective_quotes"] == 6
    assert btc["eligible_strict_touches"] == 0 and eth["eligible_strict_touches"] == 1
    assert btc["quote_coverage"] == 1


def test_recovery_keeps_missed_coverage_and_original_end_date(study):
    root, project, now = study
    with sqlite3.connect(root / "observations.sqlite") as db:
        db.execute("UPDATE quotes SET prospective=0 WHERE decision_ms=?", (BASE,))
    result = health.snapshot(root, now_ms=now, project=project)
    assert result["healthy"] and result["state"] == "实时采集正常"
    for market in result["markets"]:
        assert market["missed_decisions"] == 1
        assert market["quote_coverage"] == pytest.approx(6 / 7)
        assert market["maximum_final_coverage"] == pytest.approx(25919 / 25920)
    assert result["end_ms"] == BASE + 90 * 24 * health.HOUR


def test_health_publishing_is_separate_and_only_logs_changes(study, monkeypatch):
    root, project, now = study
    result = health.snapshot(root, now_ms=now, project=project)
    monkeypatch.setattr(health, "snapshot", lambda path: result)
    before = {p.name: p.read_bytes() for p in root.iterdir() if p.is_file()}
    health.inspect(root, write=True)
    health.inspect(root, write=True)
    assert len((root / "health_changes.jsonl").read_text().splitlines()) == 1
    for name, content in before.items():
        assert (root / name).read_bytes() == content
    page = (root / "health.html").read_text()
    assert "实时采集正常" in page and "超过三分钟未更新" in page
    assert "health.json" in page


def test_unreadable_inputs_publish_failure_instead_of_stale_success(study):
    root, _, _ = study
    (root / "status.json").write_text("broken")
    (root / "health.json").write_text("also broken")
    result = health.inspect(root, write=True)
    assert not result["healthy"] and result["problems"][0]["code"] == "unreadable"
    assert "健康检查无法读取数据" in (root / "health.html").read_text()
