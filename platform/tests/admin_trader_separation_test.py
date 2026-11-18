"""Administrator is only an operator; trading belongs to ordinary users."""

import pytest
from api.auth.security import hash_password
from api.config import Settings
from api.db.auth_repository import auth_repository
from api.db.database import Database
from api.main import create_app
from api.tenants.repository import TenantRepository
from fastapi.testclient import TestClient


pytestmark = pytest.mark.usefixtures("platform_engine_bootstrap")


def signin(http, username):
    response = http.post("/api/auth/login", json={
        "username": username, "password": "test-password-123",
    })
    assert response.status_code == 200, response.text


@pytest.fixture
def environment(tmp_path):
    settings = Settings(tmp_path / "identity.db", cookie_secure=False)
    database = Database(settings.database_path)
    database.initialize()
    repository = auth_repository(database, admin_username=settings.admin_username)
    admin = repository.create_user("admin", hash_password("test-password-123"))
    tenants = TenantRepository(database)
    assert tenants.list_tenants(admin["id"]) == []
    app = create_app(settings)
    with TestClient(app) as http:
        yield http, app, repository, tenants, admin


def test_admin_cannot_act_as_trader_or_impersonate(environment):
    http, _app, _users, tenants, admin = environment
    signin(http, "admin")
    assert http.get("/api/auth/me").json()["is_admin"] is True
    assert tenants.list_tenants(admin["id"]) == []
    assert http.get("/api/tenants").status_code == 403
    assert http.get("/api/exchange-accounts").status_code == 403
    assert http.get("/api/system/exchange-accounts").status_code == 403
    assert http.get("/api/trading/markets").status_code == 403
    assert http.post("/api/auth/impersonate", json={"username": "alice"}).status_code == 403
    assert http.post("/api/auth/impersonate/stop").status_code == 403
    assert http.get("/api/users").json() == []


def test_new_ordinary_users_get_separate_tenants_and_cannot_read_each_other(environment):
    http, app, _users, tenants, admin = environment
    signin(http, "admin")
    for username in ("alice", "bob"):
        result = http.post("/api/users", json={
            "username": username, "password": "test-password-123",
        })
        assert result.status_code == 201, result.text
    assert tenants.list_tenants(admin["id"]) == []
    assert http.get("/api/users").status_code == 200
    http.post("/api/auth/logout")
    signin(http, "alice")
    mine = http.get("/api/tenants")
    assert mine.status_code == 200
    production = [row for row in mine.json() if row["purpose"] == "production"]
    assert len(production) == 1
    created = http.post("/api/system/exchange-accounts", json={
        "exchange": "binance", "environment": "sandbox",
        "account_name": "alice-only", "api_key": "alice-key", "secret": "alice-secret",
    })
    assert created.status_code == 201, created.text
    account = created.json()
    assert [row["id"] for row in http.get("/api/exchange-accounts").json()] == [account["id"]]
    assert http.post("/api/tenants", json={"name": "extra", "purpose": "production"}).status_code == 403
    http.post("/api/auth/logout")
    signin(http, "bob")
    assert http.get("/api/exchange-accounts").json() == []
    assert http.get(f"/api/exchange-accounts/{account['id']}/portfolio").status_code == 404
    assert http.get(f"/api/trading/accounts/{account['id']}/state?symbol=BTCUSDT-PERP").status_code == 404
    assert http.delete(f"/api/system/exchange-accounts/{account['id']}").status_code == 404
    assert app.state.accounts.row(account["id"])["tenant_id"] == production[0]["id"]


def test_legacy_tenant_requires_explicit_admin_transfer(environment):
    http, _app, _users, tenants, admin = environment
    template_id = tenants.template_id_for("production")
    legacy = tenants.create_tenant(template_id, "production", "管理员原有交易", None, admin["id"])
    virtual = tenants.create_virtual_account(legacy["id"], "historical-paper", "100", "USDT")
    signin(http, "admin")
    response = http.post("/api/users", json={"username": "alice", "password": "test-password-123"})
    assert response.status_code == 201
    alice_id = response.json()["id"]
    found = http.get("/api/users/legacy-tenants")
    assert found.status_code == 200
    assert any(row["id"] == legacy["id"] for row in found.json())
    url = f"/api/users/legacy-tenants/{legacy['id']}/transfer"
    assert http.post(url, json={"target_username": "alice", "confirm": False}).status_code == 422
    assert tenants.get_tenant(legacy["id"])["owner_id"] == admin["id"]
    transferred = http.post(url, json={"target_username": "alice", "confirm": True})
    assert transferred.status_code == 200, transferred.text
    assert tenants.get_tenant(legacy["id"])["owner_id"] == alice_id
    assert tenants.list_virtual_accounts(legacy["id"])[0]["id"] == virtual["id"]
    assert not any(row["id"] == legacy["id"] for row in http.get("/api/users/legacy-tenants").json())
    http.post("/api/auth/logout")
    signin(http, "alice")
    assert any(row["id"] == legacy["id"] for row in http.get("/api/tenants").json())


def test_custom_admin_username_is_not_automatically_provisioned(tmp_path):
    database = Database(tmp_path / "custom.db")
    database.initialize()
    users = auth_repository(database, admin_username="operator")
    user = users.create_user("operator", hash_password("test-password-123"))
    assert TenantRepository(database).list_tenants(user["id"]) == []
    ordinary = users.create_user("alice", hash_password("test-password-123"))
    assert len(TenantRepository(database).list_tenants(ordinary["id"])) == 1
    database.close()
