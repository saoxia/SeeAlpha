"""Business task endpoints forward through Platform with no in-process Engine.

This route-level test uses only an isolated migrated business SQLite database;
it intentionally does not start the API's background workers or Engine service.
"""

from contextlib import asynccontextmanager
from pathlib import Path

from alembic import command
from alembic.config import Config
from api.auth.security import hash_password
from api.config import Settings
from api.db.auth_repository import auth_repository
from api.main import create_app
from fastapi.testclient import TestClient

PROJECT = Path(__file__).resolve().parents[2]


@asynccontextmanager
async def _router_only_lifespan(_app):
    """Engine startup and periodic jobs are outside this HTTP route test."""
    yield


def _client(tmp_path):
    url = f"sqlite:///{tmp_path / 'business.db'}"
    config = Config(str(PROJECT / "alembic.ini"))
    config.set_main_option("script_location", str(PROJECT / "migrations"))
    config.attributes["database_url"] = url
    command.upgrade(config, "head")
    app = create_app(Settings(
        tmp_path / "reference.db", database_url=url, cookie_secure=False
    ))
    app.state.database.initialize()
    auth_repository(app.state.database).create_user(
        "admin", hash_password("test-password-123")
    )
    auth_repository(app.state.database).create_user(
        "alice", hash_password("test-password-123")
    )
    # Exercise only auth and strategy routing. The isolated Engine is not needed.
    app.router.lifespan_context = _router_only_lifespan
    return app


def login(client, username="admin"):
    return client.post("/api/auth/login", json={
        "username": username, "password": "test-password-123",
    })


def test_admin_async_backtest_and_task_routes_forward_without_running_locally(tmp_path, monkeypatch):
    app = _client(tmp_path)
    service = app.state.strategies
    events = []
    identity = "b" * 32
    alice = app.state.repository.get_user_by_username("alice")
    tenant_id = app.state.tenants.repository.production_for_owner(alice["id"])["id"]

    def submit(tenant_id, account_id, start, end, interval, *, idempotency_key):
        events.append(("submit", tenant_id, account_id, idempotency_key))
        return {"id": identity, "kind": "backtest", "status": "PENDING"}

    monkeypatch.setattr(service, "submit_backtest", submit)
    monkeypatch.setattr(service, "tasks", lambda tenant_id: [{"id": identity, "status": "PENDING"}])
    monkeypatch.setattr(service, "task", lambda tenant_id, job_id: {
        "id": job_id, "tenant_id": str(tenant_id), "status": "SUCCEEDED",
    })
    monkeypatch.setattr(service, "task_result", lambda tenant_id, job_id: {
        "job_id": job_id, "ending_balance": "1050",
    })
    monkeypatch.setattr(service, "cancel_task", lambda tenant_id, job_id: {
        "id": job_id, "status": "CANCELLED",
    })
    monkeypatch.setattr(service, "retry_task", lambda tenant_id, job_id: {
        "id": "c" * 32, "status": "PENDING", "retry_of": job_id,
    })

    submission = {
        "start": "2024-01-01", "end": "2024-01-02", "interval": "1m",
        "idempotency_key": "study-1",
    }
    # All Engine-facing methods are replaced above; no Engine HTTP is permitted.
    def no_engine_call(*_args, **_kwargs):
        raise AssertionError("Routing test made an unexpected Engine HTTP request")

    monkeypatch.setattr(app.state.engine, "_sync", no_engine_call)
    with TestClient(app) as http:
        assert http.post(
            f"/api/tenants/{tenant_id}/accounts/1/backtests/async", json=submission,
        ).status_code == 401
        login(http, "alice")
        created = http.post(
            f"/api/tenants/{tenant_id}/accounts/1/backtests/async", json=submission,
        )
        assert created.status_code == 202, created.text
        assert created.json()["id"] == identity
        assert events == [("submit", tenant_id, 1, "study-1")]
        listing = http.get(f"/api/tenants/{tenant_id}/jobs")
        assert listing.status_code == 200 and listing.json()["items"][0]["id"] == identity
        assert http.get(f"/api/tenants/{tenant_id}/jobs/{identity}").json()["status"] == "SUCCEEDED"
        assert http.get(f"/api/tenants/{tenant_id}/jobs/{identity}/result").json()["ending_balance"] == "1050"
        assert http.post(f"/api/tenants/{tenant_id}/jobs/{identity}/cancel").json()["status"] == "CANCELLED"
        retry = http.post(f"/api/tenants/{tenant_id}/jobs/{identity}/retry")
        assert retry.status_code == 202
        assert retry.json()["retry_of"] == identity

    app.state.database.close()


def test_worker_settings_admin_only_validated_and_forwarded(tmp_path, monkeypatch):
    app = _client(tmp_path)
    service = app.state.strategies
    calls = []
    monkeypatch.setattr(service.tenants.repository, "list_tenants", lambda: [
        {"id": 7, "name": "开发 A", "purpose": "development", "status": "active"},
        {"id": 8, "name": "正式 B", "purpose": "production", "status": "active"},
        {"id": 9, "name": "已归档", "purpose": "development", "status": "archived"},
    ])
    monkeypatch.setattr(service.tenants, "require_active", lambda tenant_id: {
        "id": tenant_id, "purpose": "production" if tenant_id == 8 else "development",
        "status": "active",
    })
    def forward(method, tenant_id, suffix="", **kwargs):
        calls.append((method, tenant_id, suffix, kwargs))
        return {
            "max_workers": kwargs.get("json", {}).get("max_workers", 1),
            "timeout_seconds": kwargs.get("json", {}).get("timeout_seconds", 3600),
            "default_max_workers": 1, "default_timeout_seconds": 3600,
            "source": "saved" if method == "PUT" else "default",
            "running": 0, "queued": 0,
        }
    monkeypatch.setattr(app.state.engine, "_task_sync", forward)
    with TestClient(app) as http:
        path = "/api/tenants/7/backtest/worker-settings"
        assert http.get(path).status_code == 401
        assert http.put(path, json={"max_workers": 2, "timeout_seconds": 100}).status_code == 401
        login(http)
        available = http.get("/api/admin/backtest-workers/tenants")
        assert available.status_code == 200
        assert [row["id"] for row in available.json()["items"]] == [7]
        original = http.get(path)
        assert original.status_code == 200 and original.json()["max_workers"] == 1
        assert http.put(path, json={"max_workers": 9, "timeout_seconds": 100}).status_code == 422
        assert http.put(path, json={"max_workers": True, "timeout_seconds": 100}).status_code == 422
        assert http.put(path, json={"max_workers": 2, "timeout_seconds": 100, "extra": 1}).status_code == 422
        assert http.get("/api/tenants/8/backtest/worker-settings").status_code == 409
        saved = http.put(path, json={"max_workers": 2, "timeout_seconds": 1800})
        assert saved.status_code == 200
        assert saved.json()["max_workers"] == 2
        assert calls == [
            ("GET", 7, "/worker-settings", {}),
            ("PUT", 7, "/worker-settings", {"json": {"max_workers": 2, "timeout_seconds": 1800}}),
        ]
    app.state.database.close()
