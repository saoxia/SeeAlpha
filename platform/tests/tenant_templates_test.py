"""Empty tenant templates and default Platform tenant creation without Engine internals."""

import pytest
from alembic import command
from alembic.config import Config
from api.auth.security import hash_password
from api.config import Settings
from api.db.auth_repository import auth_repository
from api.main import create_app
from fastapi.testclient import TestClient

pytestmark = pytest.mark.usefixtures("platform_engine_bootstrap")


def login(client):
    response = client.post("/api/auth/login", json={
        "username": "admin", "password": "test-password-123",
    })
    assert response.status_code == 200, response.text


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


def create_tenant(client, purpose, name):
    if purpose == "production":
        rows = client.get("/api/tenants").json()
        return next(row for row in rows if row["purpose"] == "production")
    response = client.post("/api/tenants", json={"name": name})
    assert response.status_code == 201, response.text
    assert response.json()["purpose"] == "development"
    return response.json()


def _app_children(http, tenant_id):
    return http.app.state.tenants.repository.count_children(tenant_id)


def test_templates_are_empty_and_tenants_start_empty(client):
    http, _app = client
    rows = http.get("/api/tenants/templates").json()
    assert {row["purpose"] for row in rows} == {"production", "development"}
    for row in rows:
        assert set(row) == {"id", "purpose", "name", "description", "created_at"}
        assert row["description"] is None
    created = http.post("/api/tenants/templates", json={
        "purpose": "development", "name": "另一开发模板", "description": "仅说明",
    })
    assert created.status_code == 201, created.text
    assert set(created.json()) == {"id", "purpose", "name", "description", "created_at"}
    for purpose, name in (("production", "正式甲"), ("development", "开发甲")):
        tenant = create_tenant(http, purpose, name)
        assert tenant["purpose"] == purpose and tenant["status"] == "active"
        assert tenant["description"] is None
        assert http.get(f"/api/tenants/{tenant['id']}/accounts").json() == []
        assert http.get(f"/api/tenants/{tenant['id']}/subscriptions").json() == []
        children = _app_children(http, tenant["id"])
        assert children == {"virtual_accounts": 0, "exchange_accounts": 0, "policies": 0}
