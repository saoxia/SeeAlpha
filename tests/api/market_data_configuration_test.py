"""Date changes coordinate parameters, confirmed deletion and service recovery."""

import json
from datetime import UTC, datetime

import pytest
from nautilus_trader.model import AggressorSide, Price, Quantity, TradeId, TradeTick

from nautilus_support import TestInstrumentProvider
from pipeline.catalog import ParquetDataCatalog
from pipeline.models import PipelineSettings
from pipeline.pair_deletion import trim_pair_history
from tests.api.auth_test import login
from tests.api.market_data_test import context

__all__ = ["context"]


@pytest.fixture
def configured(context, monkeypatch):
    client, app, root = context
    login(client)
    manager = app.state.market_data
    starts = []
    monkeypatch.setattr(manager.subscriptions, "_start", starts.append)
    payload = {"symbol": "ETHUSDT", "base_level": "tick", "realtime_enabled": False,
               "history_enabled": True, "history_mode": "start", "history_start": "2026-08-01T00:00:00Z"}
    response = client.post("/api/data/subscriptions", json=payload)
    assert response.status_code == 200, response.text
    settings = PipelineSettings("futures_um", "trades", ("ETHUSDT",), ("1m",),
                                root / "data/raw", root / "data/catalog", root / "data/state")
    catalog = ParquetDataCatalog(settings.catalog_dir)
    instrument = TestInstrumentProvider.ethusdt_perp_binance()
    catalog.write_data([instrument])
    ticks = []
    source = settings.raw_dir / "futures/um/daily/trades/ETHUSDT"
    source.mkdir(parents=True)
    for index, day in enumerate(("2026-08-01", "2026-08-20")):
        stamp = int(datetime.fromisoformat(day).replace(tzinfo=UTC).timestamp() * 1e9)
        ticks.append(TradeTick(instrument.id, Price.from_str("10.00"), Quantity.from_str("1.000"),
                               AggressorSide.BUY, TradeId(str(index + 1)), stamp, stamp))
        (source / f"ETHUSDT-trades-{day}.zip").write_bytes(b"archive")
    catalog.write_data(ticks)
    return client, manager, root, settings, catalog, source, payload, starts


def test_earlier_start_saves_and_restarts_history_without_deleting(configured):
    client, manager, _root, _settings, catalog, source, payload, starts = configured
    response = client.post("/api/data/subscriptions", json={**payload, "history_start": "2026-07-01T00:00:00Z"})
    assert response.status_code == 200 and "job" not in response.json()
    assert len(catalog.trade_ticks()) == 2 and len(list(source.glob("*.zip"))) == 2
    saved = manager.policy_repository.list()[0]
    assert saved["history_start"].startswith("2026-07-01") and saved["history_enabled"]
    assert starts[-1].history_start == datetime(2026, 7, 1, tzinfo=UTC)


def test_later_start_requires_bound_confirmation_and_rejects_changed_sources(configured):
    client, manager, _root, _settings, catalog, source, payload, _starts = configured
    desired = {**payload, "history_start": "2026-08-15T00:00:00Z"}
    assert client.post("/api/data/subscriptions", json=desired).status_code == 409
    saved = manager.policy_repository.list()[0]
    assert not saved["history_enabled"] and saved["history_start"].startswith("2026-08-01")
    plan = client.post("/api/data/subscriptions/plan", json=desired)
    assert plan.status_code == 200, plan.text
    plan = plan.json()
    assert plan["requires_confirmation"] and plan["deletion"]["delete_archives"]
    assert client.post("/api/data/subscriptions", json={**desired, "deletion_snapshot": plan["deletion"]["snapshot"],
        "confirmation": "WRONG"}).status_code == 422
    (source / "ETHUSDT-trades-2026-08-01.zip").write_bytes(b"changed source")
    assert client.post("/api/data/subscriptions", json={**desired, "deletion_snapshot": plan["deletion"]["snapshot"],
        "confirmation": "DELETE ETHUSDT"}).status_code == 409
    assert len(catalog.trade_ticks()) == 2
    login(client, username="viewer")
    assert client.post("/api/data/subscriptions/plan", json=desired).status_code == 403
    login(client)
    assert client.post("/api/data/subscriptions/plan", json=desired, headers={"origin": "https://evil.example"}).status_code == 403


@pytest.mark.parametrize("succeed", [True, False])
def test_new_start_applies_only_after_successful_trim(configured, monkeypatch, succeed):
    client, manager, root, settings, catalog, source, payload, starts = configured
    desired = {**payload, "history_start": "2026-08-15T00:00:00Z"}
    plan = client.post("/api/data/subscriptions/plan", json=desired).json()

    class Process:
        returncode = 0 if succeed else 1
        def wait(self, **kwargs):
            return self.returncode
        def poll(self):
            return self.returncode

    def spawn(command, **kwargs):
        assert command[command.index("-m") + 1:command.index("--request")] == ["pipeline.data_management", "trim"]
        assert len(kwargs["pass_fds"]) == 2
        parameters = json.loads(command[command.index("--request") + 1])
        if succeed:
            result = trim_pair_history(parameters["symbol"], parameters["product"],
                datetime.fromisoformat(parameters["policy"]["history_start"]), settings,
                expected_snapshot=parameters["deletion_snapshot"], project=root)
            identity = command[command.index("--job-id") + 1]
            manager.directory.mkdir(parents=True, exist_ok=True)
            (manager.directory / f"{identity}.json").write_text(json.dumps(result))
        return Process()

    monkeypatch.setattr("api.market_data.service.subprocess.Popen", spawn)
    response = client.post("/api/data/subscriptions", json={**plan["policy"], "deletion_snapshot": plan["deletion"]["snapshot"],
                                                            "confirmation": "DELETE ETHUSDT"})
    assert response.status_code == 200, response.text
    identity = response.json()["job"]["id"]
    manager.threads[-1].join(timeout=3)
    job = manager.job(identity)
    assert job["status"] == ("succeeded" if succeed else "failed")
    saved = manager.policy_repository.list()[0]
    if succeed:
        assert saved["history_start"].startswith("2026-08-15") and saved["history_enabled"]
        assert [str(tick.trade_id) for tick in catalog.trade_ticks()] == ["2"]
        assert not (source / "ETHUSDT-trades-2026-08-01.zip").exists()
        assert (source / "ETHUSDT-trades-2026-08-20.zip").exists()
        assert starts[-1].history_start == datetime(2026, 8, 15, tzinfo=UTC)
    else:
        assert saved["history_start"].startswith("2026-08-01") and not saved["history_enabled"]
        assert len(catalog.trade_ticks()) == 2 and len(starts) == 1
    assert ("futures_um", "ETHUSDT") not in manager.subscriptions.deleting


def test_cancelled_trim_keeps_old_start_and_services_paused(configured, monkeypatch):
    import threading

    client, manager, _root, _settings, catalog, _source, payload, starts = configured
    release = threading.Event()
    desired = {**payload, "history_start": "2026-08-15T00:00:00Z"}
    plan = client.post("/api/data/subscriptions/plan", json=desired).json()

    class Process:
        returncode = None
        def wait(self, **kwargs):
            assert release.wait(5)
            self.returncode = 1
            return self.returncode
        def poll(self):
            return self.returncode

    monkeypatch.setattr("api.market_data.service.subprocess.Popen", lambda *args, **kwargs: Process())
    monkeypatch.setattr(manager, "_stop", lambda process: release.set())
    response = client.post("/api/data/subscriptions", json={**plan["policy"], "deletion_snapshot": plan["deletion"]["snapshot"],
                                                            "confirmation": "DELETE ETHUSDT"})
    assert response.status_code == 200, response.text
    identity = response.json()["job"]["id"]
    try:
        assert client.post("/api/data/subscriptions", json={**payload, "history_start": "2026-07-01T00:00:00Z"}).status_code == 409
        assert client.get("/api/data/datasets?all_levels=true").status_code == 409
        assert client.post(f"/api/data/jobs/{identity}/cancel").status_code == 200
    finally:
        release.set()
    manager.threads[-1].join(timeout=3)
    assert manager.job(identity)["status"] == "cancelled"
    saved = manager.policy_repository.list()[0]
    assert saved["history_start"].startswith("2026-08-01") and not saved["history_enabled"]
    assert len(catalog.trade_ticks()) == 2 and len(starts) == 1
