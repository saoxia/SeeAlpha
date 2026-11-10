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
    # Exercise only auth and strategy routing. The isolated Engine is not needed.
    app.router.lifespan_context = _router_only_lifespan
    return app


def login(client):
    return client.post("/api/auth/login", json={
        "username": "admin", "password": "test-password-123",
    })


def test_admin_async_backtest_and_task_routes_forward_without_running_locally(tmp_path, monkeypatch):
    app = _client(tmp_path)
    service = app.state.strategies
    events = []
    identity = "b" * 32

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
            "/api/tenants/1/accounts/1/backtests/async", json=submission,
        ).status_code == 401
        login(http)
        created = http.post(
            "/api/tenants/1/accounts/1/backtests/async", json=submission,
        )
        assert created.status_code == 202, created.text
        assert created.json()["id"] == identity
        assert events == [("submit", 1, 1, "study-1")]
        listing = http.get("/api/tenants/1/jobs")
        assert listing.status_code == 200 and listing.json()["items"][0]["id"] == identity
        assert http.get(f"/api/tenants/1/jobs/{identity}").json()["status"] == "SUCCEEDED"
        assert http.get(f"/api/tenants/1/jobs/{identity}/result").json()["ending_balance"] == "1050"
        assert http.post(f"/api/tenants/1/jobs/{identity}/cancel").json()["status"] == "CANCELLED"
        retry = http.post(f"/api/tenants/1/jobs/{identity}/retry")
        assert retry.status_code == 202
        assert retry.json()["retry_of"] == identity

    app.state.database.close()
