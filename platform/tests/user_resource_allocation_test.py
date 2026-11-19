"""Admin allocation, persistence and runtime access boundaries.

Uses an isolated ORM SQLite database; no real Docker or model invocation.
"""
from __future__ import annotations

import pytest
from alembic import command
from alembic.config import Config
from api.auth.security import hash_password
from api.config import Settings
from api.db.auth_repository import auth_repository
from api.main import create_app
from api.user_resources import UserResources
from fastapi.testclient import TestClient


def auth(client, username):
    response = client.post("/api/auth/login", json={
        "username": username, "password": "test-password-123",
    })
    assert response.status_code == 200


@pytest.fixture
def api_client(tmp_path, platform_engine_bootstrap):
    url = "sqlite:///" + str(tmp_path / "business.db")
    cfg = Config("alembic.ini")
    cfg.attributes["database_url"] = url
    command.upgrade(cfg, "head")
    app = create_app(Settings(tmp_path / "reference.db", database_url=url, cookie_secure=False))
    app.state.database.initialize()
    auth_repository(app.state.database).create_user("admin", hash_password("test-password-123"))
    with TestClient(app) as client:
        auth(client, "admin")
        yield client, app


def allocation(*, harness=True, development=True, production=True):
    def resource(enabled):
        return {"enabled": enabled, "cpus": None, "memory": None, "node": None}
    return {
        "harness": resource(harness),
        "development": resource(development),
        "production": resource(production),
    }


def test_admin_allocation_persists_and_gates_user_routes(api_client):
    client, app = api_client
    created = client.post("/api/users", json={
        "username": "alice", "password": "test-password-123",
    })
    assert created.status_code == 201, created.text
    user = created.json()
    uid = user["id"]
    assert all(row["enabled"] for row in user["resources"].values())
    assert client.get("/api/users/resource-capabilities").status_code == 200

    disabled = allocation(harness=False, development=False, production=False)
    changed = client.put(f"/api/users/{uid}/resources", json=disabled)
    assert changed.status_code == 200, changed.text
    assert changed.json()["resources"] == disabled
    assert UserResources(app.state.database).get(uid) == disabled
    assert client.get("/api/users").json()[0]["resources"] == disabled
    assert client.put("/api/users/9999/resources", json=disabled).status_code == 404

    auth(client, "alice")
    assert client.get("/api/users/resource-capabilities").status_code == 403
    assert client.put(f"/api/users/{uid}/resources", json=allocation()).status_code == 403
    assert client.post("/api/tenants", json={"name": "blocked-dev"}).status_code == 403
    assert client.get("/api/harness/status").status_code == 403


def test_invalid_quota_and_node_do_not_change_allocation(api_client):
    client, _app = api_client
    user = client.post("/api/users", json={
        "username": "alice", "password": "test-password-123",
    }).json()
    uid = user["id"]
    bad = allocation()
    bad["harness"]["cpus"] = "999"
    assert client.put(f"/api/users/{uid}/resources", json=bad).status_code == 422
    bad = allocation()
    bad["development"]["memory"] = "9999g"
    assert client.put(f"/api/users/{uid}/resources", json=bad).status_code == 422
    bad = allocation()
    bad["harness"]["node"] = "not-a-real-node"
    assert client.put(f"/api/users/{uid}/resources", json=bad).status_code == 422
    assert client.get("/api/users").json()[0]["resources"] == allocation()


def test_resource_overrides_must_precede_existing_development_tenant(api_client):
    client, app = api_client
    user = client.post("/api/users", json={
        "username": "alice", "password": "test-password-123",
    }).json()
    uid = user["id"]
    values = allocation()
    values["development"]["cpus"] = "2.5"
    values["development"]["memory"] = "6g"
    saved = client.put(f"/api/users/{uid}/resources", json=values)
    assert saved.status_code == 200, saved.text
    assert app.state.user_resources.get(uid)["development"]["cpus"] == "2.5"
    assert app.state.user_resources.for_tenant(
        user["production_tenant_id"], "production"
    )["enabled"]

    auth(client, "alice")
    tenant = client.post("/api/tenants", json={"name": "dev"}).json()
    assert tenant["purpose"] == "development"
    assert app.state.user_resources.for_tenant(tenant["id"], "development")["memory"] == "6g"

    auth(client, "admin")
    values["development"]["cpus"] = "3"
    assert client.put(f"/api/users/{uid}/resources", json=values).status_code == 409
    assert app.state.user_resources.get(uid)["development"]["cpus"] == "2.5"
    values["development"]["cpus"] = "2.5"
    values["development"]["enabled"] = False
    assert client.put(f"/api/users/{uid}/resources", json=values).status_code == 200
    auth(client, "alice")
    assert client.post("/api/tenants", json={"name": "second-dev"}).status_code == 403
    assert client.post(f"/api/tenants/{tenant['id']}/subscriptions", json={
        "symbol": "ETHUSDT",
    }).status_code == 403


def test_inherited_defaults_do_not_allocate_docker_on_user_creation(api_client):
    client, app = api_client
    user = client.post("/api/users", json={
        "username": "newuser", "password": "test-password-123",
    }).json()
    assert user["engine_node"] is None
    assert app.state.user_resources.get(user["id"]) == allocation()
