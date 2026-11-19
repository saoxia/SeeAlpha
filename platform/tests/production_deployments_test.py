"""Admin-driven production Docker placement, persistence and auth.

No Docker daemon, remote Node process or live exchange required.
"""
from __future__ import annotations

import json
import stat

import httpx
import pytest
from alembic import command
from alembic.config import Config
from api.auth.security import hash_password
from api.config import Settings
from api.db.auth_repository import auth_repository
from api.main import create_app
from api.node.hub import NodeHub
from api.production_deployments import ProductionDeploymentManager
from api.tenant_docker import DockerTenantManager
from core.engine_contracts import ENGINE_CONTRACT_HEADER, ENGINE_CONTRACT_VERSION, EngineConflict
from fastapi.testclient import TestClient


class StubManager:
    def __init__(self, hub=None):
        self.hub = hub
        self.ensured = []
        self.market_tenant_id = None

    def bind_market_tenant(self, tenant_id):
        self.market_tenant_id = str(tenant_id)

    def ensure(self, tenant_id, purpose, status="active"):
        node = self.hub.assigned(tenant_id) if self.hub else "local"
        self.ensured.append((str(tenant_id), purpose, status, node))

    def endpoint(self, tenant_id):
        return "http://127.0.0.1:8766", "token"

    def stop(self, tenant_id, **kwargs):
        return None

    def worker_log(self, tenant_id, path):
        return str(path)


def test_user_production_requires_selection_and_persists_immutable_remote_placement(tmp_path):
    config = tmp_path / "nodes.json"
    config.write_text(json.dumps({"nodes": {"edge": "a" * 48}, "tenant_nodes": {}}))
    config.chmod(0o600)
    hub = NodeHub(config)
    runtime = StubManager(hub)
    root = tmp_path / "engines"
    manager = ProductionDeploymentManager(runtime, root, hub)
    manager.bind_market_tenant(1)

    assert manager.eligible(1, "production")
    assert manager.eligible(3, "development")
    assert not manager.eligible(2, "production")
    manager.ensure(1, "production")
    manager.ensure(3, "development")
    with pytest.raises(EngineConflict, match="先选择服务器"):
        manager.ensure(2, "production")
    assert [call[0] for call in runtime.ensured] == ["1", "3"]

    assert manager.choices() == [{"id": "local", "online": True}, {"id": "edge", "online": False}]
    with pytest.raises(ValueError, match="未连接"):
        manager.assign(2, "edge")
    assert not root.exists() or not manager.path.exists()
    with pytest.raises(ValueError, match="未注册"):
        manager.assign(2, "unknown")

    hub.authenticate("edge", "Bearer " + "a" * 48)
    assert manager.assign(2, "edge") == "edge"
    manager.ensure(2, "production")
    assert runtime.ensured[-1] == ("2", "production", "active", "edge")
    assert hub.describe()["tenant_nodes"]["2"] == "edge"
    assert stat.S_IMODE(manager.path.stat().st_mode) == 0o600
    assert manager.assign(2, "edge") == "edge"
    with pytest.raises(ValueError, match="禁止"):
        manager.assign(2, "local")
    assert manager.placement(2) == "edge"

    fresh_hub = NodeHub(config)
    fresh_manager = ProductionDeploymentManager(StubManager(fresh_hub), root, fresh_hub)
    assert fresh_manager.placement(2) == "edge"
    assert fresh_hub.assigned(2) == "edge"
    with pytest.raises(EngineConflict, match="先选择服务器"):
        fresh_manager.ensure(4, "production")


def test_placement_file_rejects_unsafe_permissions(tmp_path):
    root = tmp_path / "engines"
    root.mkdir()
    registry = root / ".production-placements.json"
    registry.write_text('{"2": "local"}')
    registry.chmod(0o644)
    with pytest.raises(ValueError, match="private"):
        ProductionDeploymentManager(StubManager(), root)
    registry.chmod(0o600)
    assert ProductionDeploymentManager(StubManager(), root).placement(2) == "local"


def test_admin_user_creation_does_not_deploy_docker_until_server_selected(
    tmp_path, monkeypatch,
):
    url = f"sqlite:///{tmp_path / 'business.db'}"
    alembic = Config("alembic.ini")
    alembic.attributes["database_url"] = url
    command.upgrade(alembic, "head")
    root = tmp_path / "engines"
    app = create_app(Settings(
        tmp_path / "reference.db", database_url=url, cookie_secure=False,
        engine_deployment="docker", engine_tenant_root=root,
    ))
    app.state.database.initialize()
    auth_repository(app.state.database).create_user("admin", hash_password("test-password-123"))
    ensure_calls = []
    monkeypatch.setattr(
        DockerTenantManager, "ensure",
        lambda self, tenant_id, purpose, status="active":
        ensure_calls.append((str(tenant_id), purpose, status)),
    )
    monkeypatch.setattr(
        DockerTenantManager, "endpoint",
        lambda self, tenant_id: ("http://127.0.0.1:8766", "seealpha-local-engine"),
    )
    def market_transport(request):
        if request.url.path.endswith("/data/control/jobs"):
            body = []
        elif request.url.path.endswith("/data/control/sync"):
            body = {"last_error": None}
        else:
            raise AssertionError(f"Unexpected Engine request: {request.url.path}")
        return httpx.Response(200, json=body, headers={
            ENGINE_CONTRACT_HEADER: ENGINE_CONTRACT_VERSION,
        })
    app.state.engine.sync_transport = httpx.MockTransport(market_transport)
    app.state.engine.async_transport = app.state.engine.sync_transport
    monkeypatch.setattr(
        app.state.engine, "_sync",
        lambda method, path, **kwargs: {"status": "active", "tenant_id": path.split("/")[2]},
    )
    with TestClient(app) as http:
        assert http.post("/api/auth/login", json={
            "username": "admin", "password": "test-password-123",
        }).status_code == 200
        created = http.post("/api/users", json={
            "username": "alice", "password": "test-password-123",
        })
        assert created.status_code == 201, created.text
        user = created.json()
        tid = str(user["production_tenant_id"])
        assert user["engine_node"] is None
        assert not any(call[0] == tid for call in ensure_calls)
        assert not app.state.production_deployments.eligible(tid, "production")
        with pytest.raises(EngineConflict, match="先选择服务器"):
            app.state.engine.ensure_tenant(tid, "production")
        assert not any(call[0] == tid for call in ensure_calls)

        nodes = http.get("/api/users/engine-nodes")
        assert nodes.status_code == 200
        assert nodes.json() == {
            "deployment": "docker", "nodes": [{"id": "local", "online": True}],
        }
        assert http.post(f"/api/users/{user['id']}/engine", json={"node_id": "edge"}).status_code == 409
        assert not any(call[0] == tid for call in ensure_calls)

        deployed = http.post(f"/api/users/{user['id']}/engine", json={"node_id": "local"})
        assert deployed.status_code == 200, deployed.text
        assert deployed.json()["engine_node"] == "local"
        assert (tid, "production", "active") in ensure_calls
        assert http.post(f"/api/users/{user['id']}/engine", json={"node_id": "local"}).status_code == 200
        assert http.post(f"/api/users/{user['id']}/engine", json={"node_id": "edge"}).status_code == 409
        assert http.get("/api/users").json()[0]["engine_node"] == "local"

        http.post("/api/auth/logout")
        http.post("/api/auth/login", json={
            "username": "alice", "password": "test-password-123",
        })
        assert http.get("/api/users/engine-nodes").status_code == 403
        assert http.post(f"/api/users/{user['id']}/engine", json={"node_id": "local"}).status_code == 403

    assert ProductionDeploymentManager(StubManager(), root).placement(tid) == "local"
