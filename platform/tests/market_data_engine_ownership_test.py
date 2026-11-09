"""Remote API manages durable business policy reconciliation without local Engine calls."""
import threading
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from api.market_data.contracts import DataJobRequest
from api.market_data.monitor import ServiceMonitor
from api.market_data.service import DataService
from api.market_data.subscriptions import Subscriptions


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


def test_platform_subscription_sync_never_starts_or_stops_workers(tmp_path):
    from api.market_data.contracts import DataPolicy

    written = []
    controls = []

    class Repository:
        def save(self, policies, *, tenant_id):
            written.append((tenant_id, [p.model_dump(mode="json") for p in policies]))

        def list(self, *, tenant_id):
            return written[-1][1] if written else []

    class Engine:
        remote = True

        def market_control(self, operation, payload=None):
            controls.append((operation, payload))
            if operation == "sync":
                return {"synced": len(payload["policies"]), "last_error": None}
            if operation == "subscriptions":
                return {"items": []}
            raise AssertionError(operation)

    manager = SimpleNamespace(
        project=tmp_path, policy_repository=Repository(), engine=Engine(),
    )
    subscription = Subscriptions(manager)
    subscription._bound_tenant_id = 42
    subscription._write([DataPolicy(symbol="ETHUSDT", realtime_enabled=False)])
    assert written[0][0] == 42
    assert controls[-1][0] == "sync"
    assert controls[-1][1]["policies"][0]["symbol"] == "ETHUSDT"
    assert not hasattr(subscription, "processes")
    assert not hasattr(subscription, "auxiliary")
    assert not hasattr(subscription, "_launch")
    assert not hasattr(subscription, "_start_options")
    assert not (tmp_path / "data").exists()

    subscription.restore()
    assert [op for op, _ in controls] == ["sync", "sync"]
    subscription.close()
    assert [op for op, _ in controls] == ["sync", "sync"]
    assert not (tmp_path / "logs").exists()


def test_platform_cannot_store_new_policy_without_an_engine(tmp_path):
    from api.market_data.contracts import DataPolicy

    saved = []
    repo = SimpleNamespace(save=lambda *args, **kwargs: saved.append((args, kwargs)))
    subscription = Subscriptions(SimpleNamespace(project=tmp_path, policy_repository=repo))
    subscription._bound_tenant_id = 42
    with pytest.raises(HTTPException) as error:
        subscription._write([DataPolicy(symbol="ETHUSDT", realtime_enabled=True)])
    assert error.value.status_code == 503
    assert saved == []
    assert not (tmp_path / ".local").exists()



def test_failed_engine_sync_rolls_back_platform_orm_policy(tmp_path):
    from api.market_data.contracts import DataPolicy

    initial = DataPolicy(symbol="ETHUSDT", realtime_enabled=False)
    storage = [initial]

    class Repository:
        def save(self, policies, *, tenant_id):
            assert tenant_id == 42
            storage[:] = list(policies)

        def list(self, *, tenant_id):
            assert tenant_id == 42
            return [p.model_dump(mode="json") for p in storage]

    class Engine:
        def market_control(self, operation, payload):
            assert operation == "sync"
            raise HTTPException(409, "该交易对正在删除")

    subscription = Subscriptions(SimpleNamespace(
        project=tmp_path, policy_repository=Repository(), engine=Engine(),
    ))
    subscription._bound_tenant_id = 42
    changed = initial.model_copy(update={"realtime_enabled": True})
    with pytest.raises(HTTPException, match="正在删除") as error:
        subscription._write([changed])
    assert error.value.status_code == 409
    assert storage == [initial]
    assert subscription._read() == [initial]
    subscription.close()


def test_failed_engine_sync_and_failed_orm_rollback_fail_closed(tmp_path):
    from api.market_data.contracts import DataPolicy

    old = DataPolicy(symbol="ETHUSDT", realtime_enabled=False)
    calls = []

    class Repository:
        def list(self, *, tenant_id):
            return [old.model_dump(mode="json")]

        def save(self, policies, *, tenant_id):
            calls.append(list(policies))
            if len(calls) == 2:
                raise OSError("database unavailable")

    class Engine:
        def market_control(self, operation, payload):
            raise HTTPException(409, "Engine deletion lease")

    subscription = Subscriptions(SimpleNamespace(
        project=tmp_path, policy_repository=Repository(), engine=Engine(),
    ))
    subscription._bound_tenant_id = 42
    with pytest.raises(HTTPException, match="回滚也失败") as error:
        subscription._write([old.model_copy(update={"realtime_enabled": True})])
    assert error.value.status_code == 503
    assert subscription.policies is None
    assert len(calls) == 2
    subscription.close()


def test_platform_market_data_has_no_engine_source_or_runtime_file_reads():
    import ast
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "api/market_data"
    for path in root.glob("*.py"):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert not node.module.startswith("engine."), (path, node.module)
            if isinstance(node, ast.Import):
                assert not any(alias.name.startswith("engine.") for alias in node.names), path
        assert "data/market_data/binance_um_1m/managed" not in source
        assert 'status.json' not in source
    assert "def observe(" not in (root / "monitor.py").read_text()
    assert "def resolve(" not in (root / "subscriptions.py").read_text()
