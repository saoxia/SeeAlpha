"""User ownership, one production tenant, copy/share, and impersonation."""

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text

from api.auth.security import hash_password
from api.config import Settings
from api.db.auth_repository import auth_repository
from api.db.models import ExchangeAccount, Tenant, User, VirtualAccount
from api.main import create_app
from tests.api.auth_test import login


@pytest.fixture
def client(tmp_path):
    url = f"sqlite:///{tmp_path / 'business.db'}"
    config = Config("alembic.ini")
    config.attributes["database_url"] = url
    command.upgrade(config, "head")
    app = create_app(Settings(tmp_path / "reference.db", database_url=url, cookie_secure=False))
    app.state.database.initialize()
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


def test_users_start_with_one_production_tenant_and_admin_can_create_more(client):
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
    listed = http.get("/api/users").json()
    assert {row["username"] for row in listed} >= {"admin", "alice"}

    extra = http.post("/api/tenants", json={"name": "第二个正式", "purpose": "production"})
    assert extra.status_code == 201, extra.text
    assert extra.json()["purpose"] == "production"
    template = next(
        row for row in http.get("/api/tenants/templates").json() if row["purpose"] == "production"
    )
    another = http.post("/api/tenants", json={"template_id": template["id"], "name": "模板正式"})
    assert another.status_code == 201, another.text
    assert http.post("/api/tenants", json={
        "name": "类型冲突", "purpose": "development", "template_id": template["id"],
    }).status_code == 422
    _, admin_count = _production_count(app, "admin")
    assert admin_count == 3

    http.post("/api/auth/logout")
    login(http, username="alice")
    denied = http.post("/api/tenants", json={"name": "越权正式", "purpose": "production"})
    assert denied.status_code == 403
    assert _production_count(app, "alice") == (user_id, 1)


def test_owner_manages_development_tenants_and_cannot_remove_production(client):
    http, _app = client
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


def test_impersonation_scopes_tenant_access_to_the_target_user(client):
    http, app = client
    http.post("/api/users", json={"username": "alice", "password": "test-password-123"})
    http.post("/api/users", json={"username": "bob", "password": "test-password-123"})
    own = http.post("/api/tenants", json={"name": "管理员开发"}).json()
    started = http.post("/api/auth/impersonate", json={"username": "alice"})
    assert started.status_code == 200, started.text
    session = started.json()
    assert session["impersonating"] is True
    assert session["user"]["username"] == "alice"
    assert session["actor"]["username"] == "admin"
    me = http.get("/api/auth/me").json()
    assert me["user"]["username"] == "alice"
    assert me["impersonating"] is True
    visible = http.get("/api/tenants").json()
    assert {row["name"] for row in visible}.isdisjoint({"管理员开发"})
    assert sum(row["purpose"] == "production" for row in visible) == 1
    created = http.post("/api/tenants", json={"name": "爱丽丝开发"})
    assert created.status_code == 201, created.text
    assert created.json()["owner_id"] == me["user"]["id"]
    assert http.get(f"/api/tenants/{own['id']}/accounts").status_code == 404
    assert http.get("/api/users").status_code == 409
    assert http.post("/api/auth/impersonate", json={"username": "bob"}).status_code == 409
    stopped = http.post("/api/auth/impersonate/stop")
    assert stopped.status_code == 200, stopped.text
    assert stopped.json()["impersonating"] is False
    assert stopped.json()["user"]["username"] == "admin"
    names = {row["name"] for row in http.get("/api/tenants").json()}
    assert "管理员开发" in names
    assert "爱丽丝开发" not in names
    http.post("/api/auth/logout")
    login(http, username="alice")
    assert http.get("/api/auth/me").json()["is_admin"] is False
    assert http.post("/api/auth/impersonate", json={"username": "bob"}).status_code == 403
    assert http.get("/api/users").status_code == 403
    alice_names = {row["name"] for row in http.get("/api/tenants").json()}
    assert "爱丽丝开发" in alice_names
    assert "管理员开发" not in alice_names
    _, alice_count = _production_count(app, "alice")
    assert alice_count == 1


def test_fresh_schema_assigns_default_to_first_user_without_merging(tmp_path):
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
    assert default.owner_id == admin.id
    assert len(owners) == 2
    assert owners[0].id == default.id
    assert owners[1].owner_id == second.id
    app.state.engine.close()
    app.state.database.close()
