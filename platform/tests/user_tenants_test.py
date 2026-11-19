"""User ownership, production tenant creation, sharing and isolation.

Real Platform database and HTTP auth; only tenant Engine persistence is stubbed.
The Engine-free suite never imports in-process Engine or trades.
"""

import pytest
from alembic import command
from alembic.config import Config
from api.auth.security import hash_password
from api.config import Settings
from api.db.auth_repository import auth_repository
from api.db.models import ExchangeAccount, Tenant, User, VirtualAccount
from api.main import create_app
from fastapi.testclient import TestClient
from sqlalchemy import select


def login(client, username="admin"):
    response = client.post("/api/auth/login", json={
        "username": username, "password": "test-password-123",
    })
    assert response.status_code == 200, response.text


@pytest.fixture
def client(tmp_path, monkeypatch, platform_engine_bootstrap):
    url = f"sqlite:///{tmp_path / 'business.db'}"
    config = Config("alembic.ini")
    config.attributes["database_url"] = url
    command.upgrade(config, "head")
    app = create_app(Settings(tmp_path / "reference.db", database_url=url, cookie_secure=False))
    app.state.database.initialize()
    # Business tenant ownership is under test; Engine export/import and
    # subscription persistence are tested in cross-process integration.
    monkeypatch.setattr(app.state.engine, "note_subscription", lambda *args, **kwargs: None)
    monkeypatch.setattr(app.state.engine, "export_tenant", lambda tenant_id: {"tables": {}})
    monkeypatch.setattr(app.state.engine, "drop_tenant", lambda tenant_id, *, remove=False: None)
    monkeypatch.setattr(app.state.engine, "import_tenant", lambda tenant_id, purpose, payload: None)
    auth_repository(app.state.database).create_user("admin", hash_password("test-password-123"))
    with TestClient(app) as test_client:
        login(test_client)
        yield test_client, app


def _production_count(app, username):
    with app.state.database.session() as session:
        user = session.scalar(select(User).where(User.username == username))
        rows = session.scalars(
            select(Tenant).where(Tenant.owner_id == user.id, Tenant.purpose == "production")
        ).all()
        return user.id, len(rows)


def test_users_start_with_separate_production_and_admin_has_none(client):
    http, app = client
    created = http.post("/api/users", json={"username": "alice", "password": "test-password-123"})
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["username"] == "alice"
    assert body["production_tenant_name"]
    assert "password" not in body
    user_id, count = _production_count(app, "alice")
    assert count == 1
    assert body["production_tenant_id"]
    assert {row["username"] for row in http.get("/api/users").json()} == {"alice"}
    _, admin_count = _production_count(app, "admin")
    assert admin_count == 0
    assert http.get("/api/tenants").status_code == 403
    assert http.post("/api/tenants", json={"name": "管理员正式", "purpose": "production"}).status_code == 403

    login(http, username="alice")
    assert http.post("/api/tenants", json={"name": "额外正式", "purpose": "production"}).status_code == 403
    assert _production_count(app, "alice") == (user_id, 1)
    assert http.get("/api/tenants/templates").status_code == 403



def test_owner_manages_development_tenants_and_cannot_remove_production(client):
    http, _app = client
    created_user = http.post("/api/users", json={"username": "alice", "password": "test-password-123"})
    assert created_user.status_code == 201
    login(http, "alice")
    created = http.post("/api/tenants", json={"name": "研究甲", "description": "试策略"})
    assert created.status_code == 201, created.text
    tenant = created.json()
    assert tenant["purpose"] == "development"
    assert tenant["owner_id"] == http.get("/api/auth/me").json()["user"]["id"]
    production = next(row for row in http.get("/api/tenants").json() if row["purpose"] == "production")
    assert http.post(f"/api/tenants/{production['id']}/archive").status_code == 409
    assert http.delete(f"/api/tenants/{production['id']}").status_code == 409
    archived = http.post(f"/api/tenants/{tenant['id']}/archive")
    assert archived.status_code == 200
    assert archived.json()["status"] == "archived"
    restored = http.post(f"/api/tenants/{tenant['id']}/restore")
    assert restored.status_code == 200
    assert http.delete(f"/api/tenants/{tenant['id']}").status_code == 204
    assert all(row["id"] != tenant["id"] for row in http.get("/api/tenants").json())
    assert any(row["id"] == production["id"] for row in http.get("/api/tenants").json())


def test_copy_and_share_keep_subscriptions_and_drop_secrets(client):
    http, app = client
    http.post("/api/users", json={"username": "bob", "password": "test-password-123"})
    http.post("/api/users", json={"username": "alice", "password": "test-password-123"})
    login(http, "alice")
    production = next(row for row in http.get("/api/tenants").json() if row["purpose"] == "production")
    source = http.post("/api/tenants", json={"name": "待复制"}).json()
    saved = http.post(f"/api/tenants/{source['id']}/subscriptions", json={
        "symbol": "ETHUSDT", "product": "futures_um", "base_level": "tick",
        "realtime_enabled": False, "history_enabled": True, "history_mode": "start",
        "history_start": "2024-01-01T00:00:00Z",
    })
    assert saved.status_code == 200, saved.text
    virtual = http.post(f"/api/tenants/{source['id']}/accounts", json={
        "account_name": "paper", "initial_capital": "1000.50", "quote_currency": "USDT",
    })
    assert virtual.status_code == 201, virtual.text
    keyed = http.post(f"/api/tenants/{production['id']}/accounts", json={
        "account_name": "desk", "exchange": "binance", "environment": "production",
        "api_key": "fixture-key-copy-check", "secret": "fixture-secret-copy-check",
    })
    assert keyed.status_code == 201, keyed.text
    copied = http.post(f"/api/tenants/{production['id']}/copy", json={"name": "复制开发"})
    assert copied.status_code == 201, copied.text
    copy = copied.json()
    assert copy["purpose"] == "development"
    assert copy["owner_id"] == production["owner_id"]
    assert "fixture-key-copy-check" not in copied.text
    assert "fixture-secret-copy-check" not in copied.text
    assert http.get(f"/api/tenants/{copy['id']}/accounts").json() == []
    symbols = http.get(f"/api/tenants/{copy['id']}/subscriptions").json()
    assert symbols == []
    shared_source = http.post(f"/api/tenants/{source['id']}/share", json={
        "name": "分享给bob", "username": "bob",
    })
    assert shared_source.status_code == 201, shared_source.text
    shared = shared_source.json()
    assert shared["purpose"] == "development"
    assert "1000.50" not in shared_source.text
    assert "fixture-key-copy-check" not in shared_source.text
    subscriptions = http.get(f"/api/tenants/{shared['id']}/subscriptions")
    assert subscriptions.status_code == 404
    http.post("/api/auth/logout")
    login(http, username="bob")
    bob_tenants = http.get("/api/tenants").json()
    assert sum(row["purpose"] == "production" for row in bob_tenants) == 1
    received = next(row for row in bob_tenants if row["name"] == "分享给bob")
    assert received["purpose"] == "development"
    assert http.get(f"/api/tenants/{received['id']}/accounts").json() == []
    copied_symbols = http.get(f"/api/tenants/{received['id']}/subscriptions").json()
    assert copied_symbols[0]["symbol"] == "ETHUSDT"
    assert "2024-01-01" in str(copied_symbols[0]["history_start"])
    assert http.get(f"/api/tenants/{source['id']}/subscriptions").status_code == 404
    with app.state.database.session() as session:
        accounts = session.scalars(
            select(ExchangeAccount).where(ExchangeAccount.tenant_id == received["id"])
        ).all()
        virtual_rows = session.scalars(
            select(VirtualAccount).where(VirtualAccount.tenant_id == received["id"])
        ).all()
        assert accounts == [] and virtual_rows == []
        original = session.scalar(select(ExchangeAccount).where(ExchangeAccount.account_name == "desk"))
        assert original.tenant_id == production["id"]
        assert "fixture-key-copy-check" not in original.credentials_encrypted
        assert "fixture-secret-copy-check" not in original.credentials_encrypted


def test_admin_does_not_trade_and_trader_ownership_is_scoped(client):
    http, app = client
    assert http.post("/api/users", json={"username": "alice", "password": "test-password-123"}).status_code == 201
    assert http.post("/api/users", json={"username": "bob", "password": "test-password-123"}).status_code == 201
    assert http.post("/api/auth/impersonate", json={"username": "alice"}).status_code == 404
    assert http.post("/api/auth/impersonate/stop").status_code == 404
    assert http.get("/api/tenants").status_code == 403

    login(http, "alice")
    alice = http.get("/api/auth/me").json()
    assert not alice["is_admin"]
    created = http.post("/api/tenants", json={"name": "爱丽丝开发"})
    assert created.status_code == 201, created.text
    alice_tenant = created.json()
    assert alice_tenant["owner_id"] == alice["user"]["id"]
    assert http.get("/api/users").status_code == 403

    login(http, "bob")
    bob = http.get("/api/auth/me").json()
    assert not bob["is_admin"]
    assert http.get(f"/api/tenants/{alice_tenant['id']}/accounts").status_code == 404
    bob_tenants = http.get("/api/tenants").json()
    assert all(row["id"] != alice_tenant["id"] for row in bob_tenants)
    assert sum(row["purpose"] == "production" for row in bob_tenants) == 1

    login(http, "alice")
    assert any(row["id"] == alice_tenant["id"] for row in http.get("/api/tenants").json())
    _, alice_count = _production_count(app, "alice")
    assert alice_count == 1



def test_system_tenant_is_not_owned_by_new_users(tmp_path):
    url = f"sqlite:///{tmp_path / 'business.db'}"
    config = Config("alembic.ini")
    config.attributes["database_url"] = url
    command.upgrade(config, "head")
    app = create_app(Settings(tmp_path / "reference.db", database_url=url, cookie_secure=False))
    app.state.database.initialize()
    repository = auth_repository(app.state.database)
    repository.create_user("admin", hash_password("test-password-123"))
    repository.create_user("second", hash_password("test-password-123"))
    with app.state.database.session() as session:
        admin = session.scalar(select(User).where(User.username == "admin"))
        second = session.scalar(select(User).where(User.username == "second"))
        default = session.scalar(select(Tenant).where(Tenant.system_key == "default"))
        owners = session.scalars(
            select(Tenant).where(
                Tenant.owner_id.in_([admin.id, second.id]), Tenant.purpose == "production"
            ).order_by(Tenant.id)
        ).all()
    assert default.owner_id is None
    assert len(owners) == 1
    assert owners[0].owner_id == second.id
    assert owners[0].id != default.id
    app.state.engine.close()
    app.state.database.close()
