"""Engine outages must not block Platform startup, auth, or business administration."""

import asyncio
import threading

import api.main as api_main
import httpx
import pytest
from alembic import command
from alembic.config import Config
from api.auth.security import hash_password
from api.config import Settings
from api.db.auth_repository import auth_repository
from api.engine_client import EngineClient
from api.main import create_app
from core.engine_contracts import ENGINE_CONTRACT_HEADER, ENGINE_CONTRACT_VERSION, QueryError
from fastapi import HTTPException
from fastapi.testclient import TestClient


@pytest.fixture
def app_without_engine(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'business.db'}"
    config = Config("alembic.ini")
    config.attributes["database_url"] = url
    command.upgrade(config, "head")
    app = create_app(Settings(tmp_path / "reference.db", database_url=url, cookie_secure=False))
    app.state.database.initialize()
    auth_repository(app.state.database).create_user("admin", hash_password("test-password-123"))
    monkeypatch.setattr(api_main, "_TENANT_RECONCILE_SECONDS", 0.05)
    return app


def _login(client, username="admin"):
    response = client.post("/api/auth/login", json={
        "username": username, "password": "test-password-123",
    })
    assert response.status_code == 200, response.text


def _offline(request):
    raise httpx.ConnectError("Engine offline", request=request)


def test_blocked_tenant_registration_does_not_block_startup_or_account_management(
    app_without_engine, monkeypatch,
):
    app = app_without_engine
    transport = httpx.MockTransport(_offline)
    app.state.engine.sync_transport = transport
    app.state.engine.async_transport = transport

    attempted = threading.Event()
    release = threading.Event()
    original = app.state.engine.ensure_tenant

    def blocked(*args):
        attempted.set()
        release.wait(timeout=5)
        return original(*args)

    monkeypatch.setattr(app.state.engine, "ensure_tenant", blocked)
    try:
        with TestClient(app) as client:
            assert attempted.wait(timeout=2), "the active production tenant was not reconciled"
            assert client.get("/api/health").json() == {"status": "ok"}
            _login(client)
            assert client.get("/api/auth/me").status_code == 200

            created_user = client.post("/api/users", json={
                "username": "alice", "password": "test-password-123",
            })
            assert created_user.status_code == 201, created_user.text
            assert {user["username"] for user in client.get("/api/users").json()} == {"alice"}
            _login(client, "alice")

            created_tenant = client.post("/api/tenants", json={"name": "offline-dev"})
            assert created_tenant.status_code == 201, created_tenant.text
            tenant_id = created_tenant.json()["id"]
            virtual = client.post(f"/api/tenants/{tenant_id}/accounts", json={
                "account_name": "paper", "initial_capital": "1000", "quote_currency": "USDT",
            })
            assert virtual.status_code == 201, virtual.text
            accounts = client.get(f"/api/tenants/{tenant_id}/accounts")
            assert accounts.status_code == 200 and len(accounts.json()) == 1
            assert client.get("/api/system/exchange-accounts").status_code == 200

            unavailable = client.get("/api/data/status")
            assert unavailable.status_code == 503, unavailable.text
            assert "Engine" in unavailable.json()["detail"] or "引擎" in unavailable.json()["detail"]
            backtest = client.get(
                f"/api/tenants/{tenant_id}/accounts/{virtual.json()['id']}/backtests/latest"
            )
            assert backtest.status_code == 503, backtest.text
            assert "引擎" in backtest.json()["detail"]
    finally:
        release.set()


def test_background_retries_and_registers_new_tenants_after_recovery(app_without_engine):
    app = app_without_engine
    online = threading.Event()
    failed = threading.Event()
    registered = set()
    lock = threading.Lock()
    all_registered = threading.Event()

    def reply(request):
        if request.method == "PUT" and request.url.path.startswith("/tenants/"):
            tenant_id = request.url.path.rsplit("/", 1)[-1]
            if not online.is_set():
                failed.set()
                raise httpx.ConnectError("Engine offline", request=request)
            with lock:
                registered.add(tenant_id)
                # Platform default, ordinary production, and development tenants.
                if len(registered) >= 3:
                    all_registered.set()
            return httpx.Response(
                200,
                json={"tenant_id": tenant_id, "status": "active"},
                headers={ENGINE_CONTRACT_HEADER: ENGINE_CONTRACT_VERSION},
            )
        raise httpx.ConnectError("Engine offline", request=request)

    transport = httpx.MockTransport(reply)
    app.state.engine.sync_transport = transport
    app.state.engine.async_transport = transport
    with TestClient(app) as client:
        assert failed.wait(timeout=2), "initial Engine registration must be attempted"
        assert client.get("/api/health").status_code == 200
        _login(client)
        created_user = client.post("/api/users", json={
            "username": "alice", "password": "test-password-123",
        })
        assert created_user.status_code == 201, created_user.text
        _login(client, "alice")
        created = client.post("/api/tenants", json={"name": "created-during-outage"})
        assert created.status_code == 201, created.text
        online.set()
        assert all_registered.wait(timeout=3), "reconciliation did not retry active tenants"
        with lock:
            assert "1" in registered
            assert str(created.json()["id"]) in registered
        assert client.get("/api/auth/me").status_code == 200


def test_plain_text_engine_errors_do_not_break_background_reconciliation(tmp_path):
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(500, text="Internal Server Error")
    )
    client = EngineClient(tmp_path, sync_transport=transport, async_transport=transport)
    with pytest.raises(QueryError, match="引擎请求失败"):
        client._sync("GET", "/health")
    with pytest.raises(QueryError, match="引擎请求失败"):
        asyncio.run(client._async("GET", "/health"))
    with pytest.raises(HTTPException) as exc:
        client.data_query("control/sync", {})
    assert exc.value.status_code == 502

