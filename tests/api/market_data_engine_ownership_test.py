"""Remote API manages durable business policy reconciliation without local Engine calls."""
import threading
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from api.market_data.monitor import ServiceMonitor
from api.market_data.contracts import DataJobRequest
from api.market_data.service import DataService


class FakeEngine:
    remote = True

    def __init__(self, results):
        self.calls = []
        self.results = results

    def market_control(self, operation, payload=None):
        self.calls.append((operation, payload))
        if operation == "jobs":
            return self.results
        if operation == "storage":
            return {"available": True, "writable": True}
        if operation == "job/start":
            return {"id": "a" * 32, "action": payload["payload"]["action"], "status": "running"}
        if operation == "snapshot":
            return {"type": "tasks", "tasks": [], "detail": None, "subscriptions": []}
        return {"id": "a" * 32, "status": "succeeded"}


def test_remote_api_dispatches_jobs_and_avoids_native_worker(tmp_path):
    manager = object.__new__(DataService)
    manager.project = tmp_path
    manager.lock = threading.RLock()
    manager.engine = FakeEngine([])
    manager.subscriptions = SimpleNamespace(pause_for_deletion=lambda *_: None)
    assert manager.storage()["available"] is True
    assert manager.jobs() == []
    assert manager.task_snapshot()["type"] == "tasks"

    from api.market_data.contracts import DataJobRequest
    payload = DataJobRequest(
        action="prepare", symbol="ETHUSDT", start="2026-08-01T00:00:00Z",
        end="2026-08-02T00:00:00Z", intervals=["1m"],
    )
    result = manager.start(payload)
    assert result["status"] == "running"
    assert ("job/start", {"payload": payload.model_dump(mode="json", exclude_none=True)}) in manager.engine.calls


def test_remote_completed_delete_is_applied_once_to_business_db(tmp_path):
    task = {
        "id": "b" * 32, "action": "delete_pair", "status": "succeeded",
        "parameters": {"symbol": "ETHUSDT", "product": "futures_um"},
    }
    removed = []
    manager = object.__new__(DataService)
    manager.project = tmp_path
    manager.lock = threading.RLock()
    manager.engine = FakeEngine([task])
    manager.subscriptions = SimpleNamespace(remove=lambda s, p: removed.append((s, p)))

    manager.reconcile_jobs()
    manager.reconcile_jobs()
    assert removed == [("ETHUSDT", "futures_um")]
    assert (tmp_path / ".local/state/api/market_settlements" / f"{task['id']}.json").exists()


def test_platform_monitor_only_reads_engine_and_never_creates_state_files(tmp_path):
    calls = []
    status = {"state": "DEGRADED", "reason": "monitor_expired", "items": []}

    class Engine:
        def market_control(self, operation, payload=None):
            calls.append((operation, payload))
            return status

    monitor = ServiceMonitor(SimpleNamespace(project=tmp_path, engine=Engine()))
    assert monitor.read() == status
    assert calls == [("services", None)]
    assert not hasattr(monitor, "sample")
    assert not (tmp_path / ".local/state/api/data_services").exists()

    missing = ServiceMonitor(SimpleNamespace(project=tmp_path))
    with pytest.raises(HTTPException) as error:
        missing.read()
    assert error.value.status_code == 503


def test_all_platform_job_operations_delegate_without_local_job_storage(tmp_path):
    manager = object.__new__(DataService)
    manager.project = tmp_path
    manager.lock = threading.RLock()
    manager.engine = FakeEngine([])
    manager.subscriptions = SimpleNamespace(pause_for_deletion=lambda *_: None)
    payload = DataJobRequest(
        action="prepare", symbol="ETHUSDT", start="2026-08-01T00:00:00Z",
        end="2026-08-02T00:00:00Z", intervals=["1m"],
    )
    assert manager.jobs() == []
    assert manager.task_snapshot()["type"] == "tasks"
    assert manager.start(payload)["status"] == "running"
    assert manager.job("a" * 32)["status"] == "succeeded"
    assert manager.retry("a" * 32)["status"] == "succeeded"
    assert manager.cancel("a" * 32)["status"] == "succeeded"
    names = [op for op, _ in manager.engine.calls]
    assert names == [
        "jobs", "jobs", "snapshot", "job/start",
        "job", "jobs", "job/retry", "job/cancel",
    ]
    assert not hasattr(manager, "processes")
    assert not hasattr(manager, "threads")
    assert not (tmp_path / ".local/state/api/data_jobs/operations").exists()


def test_missing_engine_fails_closed_without_local_job_execution(tmp_path):
    manager = object.__new__(DataService)
    manager.project = tmp_path
    with pytest.raises(HTTPException) as error:
        manager.jobs()
    assert error.value.status_code == 503
    with pytest.raises(HTTPException) as error:
        manager.start(DataJobRequest(
            action="prepare", symbol="ETHUSDT", start="2026-08-01T00:00:00Z",
            end="2026-08-02T00:00:00Z", intervals=["1m"],
        ))
    assert error.value.status_code == 503
    assert not (tmp_path / ".local").exists()


def test_delete_cannot_pause_business_policy_before_engine_is_available(tmp_path):
    manager = object.__new__(DataService)
    manager.project = tmp_path
    paused = []
    manager.subscriptions = SimpleNamespace(pause_for_deletion=lambda *_: paused.append(True))
    request = SimpleNamespace(action="delete_pair", symbol="ETHUSDT", product="futures_um")
    with pytest.raises(HTTPException) as error:
        manager.start(request)
    assert error.value.status_code == 503
    assert paused == []
    assert not (tmp_path / ".local").exists()
