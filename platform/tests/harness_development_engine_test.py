"""AI Harness development Engine controls: scoped routing and non-destructive reset."""
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest
from api.auth.security import hash_password
from api.config import Settings
from api.db.auth_repository import AuthRepository
from api.db.database import Database
from api.engine_client import EngineClient
from api.main import create_app
from api.research import development_engine
from api.research.development_engine import PrepareRequest, _prepare_payload
from api.tenant_docker import DockerTenantManager
from core.engine_contracts import ENGINE_CONTRACT_HEADER, ENGINE_CONTRACT_VERSION, EngineConflict
from fastapi import HTTPException
from fastapi.testclient import TestClient

pytestmark = pytest.mark.usefixtures("platform_engine_bootstrap")


def test_prepare_requires_bounded_utc_history():
    dataset = ("futures_um", "ETHUSDT", "1m")
    start = datetime(2026, 1, 1, tzinfo=UTC)
    body = PrepareRequest(
        tenant_id=11, account_id="5", dataset_ref="futures_um:ETHUSDT:1m",
        start=start, end=start + timedelta(days=7),
    )
    payload = _prepare_payload(body, dataset)
    assert payload["action"] == "prepare"
    assert payload["symbol"] == "ETHUSDT"
    assert payload["intervals"] == ["1m"]
    for end in (start, start + timedelta(days=32), datetime.now(UTC) + timedelta(days=2)):
        with pytest.raises(HTTPException) as error:
            _prepare_payload(body.model_copy(update={"end": end}), dataset)
        assert error.value.status_code == 422
    with pytest.raises(HTTPException):
        _prepare_payload(body.model_copy(update={"start": start.replace(tzinfo=None)}), dataset)


def test_tenant_data_queries_do_not_use_market_data_engine(tmp_path):
    calls = []

    class TenantManager:
        market_tenant_id = "1"

        @staticmethod
        def endpoint(tenant_id):
            return f"http://127.0.0.1:{8000 + int(tenant_id)}", f"token-for-{tenant_id}"

    def respond(request):
        calls.append((request.url.port, request.url.path, request.headers["authorization"]))
        return httpx.Response(
            200, json={"ok": True}, headers={ENGINE_CONTRACT_HEADER: ENGINE_CONTRACT_VERSION},
        )

    client = EngineClient(
        tmp_path, tenant_manager=TenantManager(),
        sync_transport=httpx.MockTransport(respond),
    )
    client.data_query("prepare-plan", {"symbol": "ETHUSDT"}, tenant_id=11)
    client.market_control("job/start", {"payload": {"action": "prepare"}}, tenant_id=11)
    client.data_query("datasets", {})
    assert calls == [
        (8011, "/tenants/11/data/prepare-plan", "Bearer token-for-11"),
        (8011, "/tenants/11/data/control/job/start", "Bearer token-for-11"),
        (8001, "/tenants/1/data/datasets", "Bearer token-for-1"),
    ]
    client.close()


def test_reset_development_preserves_data_and_backs_up_state(tmp_path, monkeypatch):
    manager = DockerTenantManager(
        tmp_path / "tenants", image="engine:pinned",
        production_cpus="1", production_memory="1g",
        development_cpus="1", development_memory="1g", log_root=tmp_path,
    )
    _root, state, data, local, env = manager._paths("11")
    for folder in (state, data, local):
        folder.mkdir(parents=True)
    state.joinpath("book.sqlite").write_text("runtime")
    local.joinpath("pid.lock").write_text("task")
    data.joinpath("catalog.parquet").write_bytes(b"historical-data")
    manager._credentials(env, "11", "development", create=True)

    def container():
        return {
            "Config": {
                "Image": "engine:pinned",
                "Labels": {
                    "seealpha.managed": "tenant-engine",
                    "seealpha.tenant": "11",
                    "seealpha.purpose": "development",
                },
            },
            "Mounts": [
                {"Destination": "/app/.local/state/engine", "Source": str(state)},
                {"Destination": "/app/.local/run", "Source": str(local)},
                {"Destination": "/app/data", "Source": str(data)},
            ],
        }

    monkeypatch.setattr(manager, "_inspect", lambda _name: container())
    stopped = []
    monkeypatch.setattr(manager, "stop", lambda tid, **kw: stopped.append((tid, kw)))
    monkeypatch.setattr(manager, "ensure", lambda tid, purpose: (
        state.mkdir(), local.mkdir(), (tid, purpose),
    ))
    backup = manager.reset_development(11)
    assert stopped == [("11", {"remove": True})]
    assert data.joinpath("catalog.parquet").read_bytes() == b"historical-data"
    assert not state.joinpath("book.sqlite").exists()
    assert (manager.root / ".resets" / backup / "state" / "book.sqlite").read_text() == "runtime"
    assert (manager.root / ".resets" / backup / "local" / "pid.lock").read_text() == "task"

    # A production credential or wrong mount cannot be reset, even if a caller
    # mistakenly selected the tenant in the UI.
    prod, _state2, _data2, _local2, cred2 = manager._paths("12")
    prod.mkdir()
    manager._credentials(cred2, "12", "production", create=True)
    with pytest.raises(EngineConflict):
        manager.reset_development(12)


def _client(tmp_path, monkeypatch):
    settings = Settings(
        tmp_path / "auth.db", cookie_secure=False, harness_enabled=True,
        harness_url="http://127.0.0.1:8777",
        harness_token="non-production-test-secret-1234567890",
        harness_owner_username="viewer",
    )
    db = Database(settings.database_path)
    db.initialize()
    users = AuthRepository(db)
    users.create_user("admin", hash_password("test-password-123"))
    users.create_user("viewer", hash_password("test-password-123"))
    db.close()
    app = create_app(settings)

    # Isolate the HTTP contract tests from Docker while retaining FastAPI
    # session and request validation. Scope/tenant authorization is independently
    # covered by research_task_access_test.py and user_tenants_test.py.
    manager = SimpleNamespace(reset_development=lambda tid: f"{tid}-backup")
    monkeypatch.setattr(
        development_engine, "_development_engine",
        lambda _request, _user, _tenant_id: manager,
    )
    monkeypatch.setattr(
        development_engine, "allowed_scope",
        lambda _app, owner, tid, account, dataset, **_kw:
        tid == 11 and account == "5" and dataset == "futures_um:ETHUSDT:1m",
    )

    calls = []
    def data_query(operation, payload, *, tenant_id=None):
        calls.append((operation, tenant_id, payload))
        return {"archive_days": 7, "missing_days": 2}

    jobs = [
        {"id": "a" * 32, "action": "prepare", "status": "succeeded",
         "parameters": {"product": "futures_um", "symbol": "ETHUSDT", "intervals": ["1m"]}},
        {"id": "b" * 32, "action": "prepare", "status": "succeeded",
         "parameters": {"product": "futures_um", "symbol": "BTCUSDT", "intervals": ["1m"]}},
    ]
    def control(operation, payload=None, *, tenant_id=None):
        calls.append((operation, tenant_id, payload))
        if operation == "jobs":
            return jobs
        if operation == "job/start":
            return {"id": "a" * 32, "status": "running"}
        return {"id": "a" * 32, "status": "cancelled"}

    monkeypatch.setattr(app.state.engine, "data_query", data_query)
    monkeypatch.setattr(app.state.engine, "market_control", control)
    monkeypatch.setattr(app.state.engine, "list_tasks", lambda _tid: [])
    monkeypatch.setattr(app.state.engine, "ensure_tenant", lambda *_args: {"status": "active"})

    @contextmanager
    def harness(_request, _user):
        yield SimpleNamespace(recent_tasks=lambda: [{"status": "completed"}])
    monkeypatch.setattr(development_engine, "_client", harness)
    return app, calls, jobs


def test_harness_data_routes_are_session_gated_and_tenant_scoped(tmp_path, monkeypatch):
    app, calls, jobs = _client(tmp_path, monkeypatch)
    selection = {"tenant_id": 11, "account_id": "5", "dataset_ref": "futures_um:ETHUSDT:1m"}
    period = {**selection, "start": "2026-01-01T00:00:00Z", "end": "2026-01-08T00:00:00Z"}
    with TestClient(app) as client:
        assert client.post("/api/harness/development-engine/data/plan", json=period).status_code == 401
        login = client.post("/api/auth/login", json={
            "username": "viewer", "password": "test-password-123",
        })
        assert login.status_code == 200
        root = "/api/harness/development-engine"
        plan = client.post(root + "/data/plan", json=period)
        assert plan.status_code == 200 and plan.json()["missing_days"] == 2
        prepared = client.post(root + "/data/prepare", json=period)
        assert prepared.status_code == 202
        assert any(call[0] == "job/start" and call[1] == 11 for call in calls)
        listed = client.get(root + "/data/jobs", params=selection)
        assert listed.status_code == 200
        assert [job["id"] for job in listed.json()["jobs"]] == ["a" * 32]
        assert client.post(root + "/data/jobs/" + "b" * 32 + "/cancel", json=selection).status_code == 404
        assert client.post(root + "/data/jobs/" + "a" * 32 + "/cancel", json=selection).status_code == 200
        assert client.post(root + "/data/plan", json={**period, "dataset_ref": "futures_um:BTCUSDT:1m"}).status_code == 403
        assert client.post(root + "/data/plan", json={**period, "end": "2026-03-01T00:00:00Z"}).status_code == 422
        assert client.post(root + "/reset", json={"tenant_id": 11, "confirmation": "RESET ENGINE 7"}).status_code == 422
        reset = client.post(root + "/reset", json={"tenant_id": 11, "confirmation": "RESET ENGINE 11"})
        assert reset.status_code == 200 and reset.json()["historical_data_preserved"] is True

        jobs.append({"id": "c" * 32, "action": "prepare", "status": "running",
                     "parameters": {"symbol": "ETHUSDT"}})
        assert client.post(root + "/reset", json={"tenant_id": 11, "confirmation": "RESET ENGINE 11"}).status_code == 409
