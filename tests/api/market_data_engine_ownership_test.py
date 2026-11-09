"""Remote API manages durable business policy reconciliation without local Engine calls."""
import threading
from types import SimpleNamespace

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
    assert (manager.directory / "settled" / f"{task['id']}.json").exists()
