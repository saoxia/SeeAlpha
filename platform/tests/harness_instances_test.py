"""Per-user Harness placement and protocol tests; no Docker or model calls."""
from __future__ import annotations

import base64
import json
import stat

import httpx
import pytest

from api.config import Settings
from api.db.auth_repository import AuthRepository
from api.db.database import Database
from api.auth.security import hash_password, token_digest
from api.main import create_app
from api.research.harness_client import HARNESS_HEADER
from api.research.harness_instances import UserHarnessInstances
from fastapi.testclient import TestClient

pytestmark = pytest.mark.usefixtures("platform_engine_bootstrap")
TASK = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"


def instance_manager(tmp_path, hub=None):
    return UserHarnessInstances(
        root=tmp_path / "harness-users", image="seealpha-harness:ci",
        cpus="2", memory="4g", hub=hub,
    )


def test_two_users_have_independent_tokens_and_workload_specs(tmp_path, monkeypatch):
    manager = instance_manager(tmp_path)
    seen = []

    def fake_execute(action, payload):
        seen.append((action, payload))
        if action == "ensure":
            return {"running": True}
        assert action == "request"
        assert payload["path"] == "/health"
        return {"status": 200, "body": {
            "service": "seealpha-harness", "contract_version": 1,
            "runtime_configured": False, "runtime_layout": "resident-container",
        }, "headers": {HARNESS_HEADER: "1"}}

    monkeypatch.setattr(manager.local, "execute", fake_execute)
    a = manager.client(1)
    b = manager.client(2)
    try:
        assert not a.health()["runtime_configured"]
        assert b.health()["runtime_layout"] == "resident-container"
    finally:
        a.close()
        b.close()
    specs = [payload for action, payload in seen if action == "ensure"]
    assert specs[0]["id"] == "harness-user-1"
    assert specs[1]["id"] == "harness-user-2"
    assert specs[0]["env"]["HARNESS_API_TOKEN"] != specs[1]["env"]["HARNESS_API_TOKEN"]
    assert specs[0]["read_only"] and specs[0]["require_nonroot"]
    assert specs[0]["mounts"] == {"state": "/app/state"}
    assert "DEEPSEEK_API_KEY" not in specs[0]["env"]
    assert all(action != "stop" for action, _ in seen)
    assert manager._token(1) == specs[0]["env"]["HARNESS_API_TOKEN"]
    token_path = tmp_path / "harness-users" / "credentials" / "user-1.token"
    assert stat.S_IMODE(token_path.stat().st_mode) == 0o600
    assert all(action == "ensure" or payload["id"] in {"harness-user-1", "harness-user-2"}
               for action, payload in seen)


def test_remote_uses_existing_node_hub_and_relays_harness_contract_and_binary(tmp_path):
    class Hub:
        def __init__(self):
            self.calls = []

        def harness_assigned(self, user_id):
            return "edge" if user_id == 7 else "local"

        def call(self, node, action, payload, **kwargs):
            self.calls.append((node, action, payload))
            if action == "ensure":
                return {"running": True}
            assert node == "edge"
            assert payload["id"] == "harness-user-7"
            assert payload["headers"][HARNESS_HEADER] == "1"
            assert payload["headers"]["Authorization"].startswith("Bearer ")
            if payload["path"].endswith("/summary-1"):
                return {"status": 200, "binary_b64": base64.b64encode(b"data").decode(),
                        "headers": {HARNESS_HEADER: "1"}}
            if payload["path"] == "/v1/tasks":
                return {"status": 202, "body": {
                    "id": TASK, "status": "queued", "turn": 1,
                }, "headers": {HARNESS_HEADER: "1"}}
            return {"status": 404, "body": {}, "headers": {HARNESS_HEADER: "1"}}

    hub = Hub()
    manager = instance_manager(tmp_path, hub)
    client = manager.client(7)
    try:
        task = client.create("Research", queue_if_busy=True)
        assert task["id"] == TASK
        assert "prompt" not in task
        assert client.download_artifact(TASK, "summary-1") == b"data"
    finally:
        client.close()
    assert hub.calls[0][1] == "ensure"
    assert all(node == "edge" for node, _, _ in hub.calls)
    assert hub.calls[1][2]["json"] == {"prompt": "Research", "queue_if_busy": True}


def test_bad_ids_and_private_credentials_fail_closed(tmp_path):
    manager = instance_manager(tmp_path)
    for invalid in (0, -1, "7", True):
        with pytest.raises(ValueError, match="user ID"):
            manager.ensure(invalid)
    token_path = tmp_path / "harness-users" / "credentials" / "user-1.token"
    token_path.parent.mkdir(mode=0o700)
    token_path.symlink_to(tmp_path / "untrusted")
    with pytest.raises(ValueError, match="trusted"):
        manager.ensure(1)


def test_per_user_api_routes_to_actor_and_rejects_impersonation(tmp_path, monkeypatch):
    settings = Settings(
        tmp_path / "auth.db", cookie_secure=False, harness_enabled=True,
        harness_deployment="per_user", harness_docker_image="seealpha-harness:ci",
        harness_root=tmp_path / "harness-users",
    )
    db = Database(settings.database_path)
    db.initialize()
    repo = AuthRepository(db)
    repo.create_user("admin", hash_password("test-password-123"))
    repo.create_user("viewer", hash_password("test-password-123"))
    repo.create_user("alice", hash_password("test-password-123"))
    users = {row["username"]: row["id"] for row in repo.list_users()}
    db.close()
    seen = []

    def fake_client(self, user_id):
        seen.append(user_id)

        def respond(request):
            assert request.headers[HARNESS_HEADER] == "1"
            if request.url.path == "/health":
                return httpx.Response(200, json={
                    "service": "seealpha-harness", "contract_version": 1,
                    "runtime_configured": False, "runtime_layout": "resident-container",
                }, headers={HARNESS_HEADER: "1"})
            return httpx.Response(202, json={"id": TASK, "status": "queued", "turn": 1},
                                  headers={HARNESS_HEADER: "1"})

        from api.research.harness_client import HarnessClient
        return HarnessClient("http://127.0.0.1:8777", "a" * 48,
                             transport=httpx.MockTransport(respond))

    monkeypatch.setattr(UserHarnessInstances, "client", fake_client)
    with TestClient(create_app(settings)) as client:
        assert client.post("/api/auth/login", json={
            "username": "admin", "password": "test-password-123",
        }).status_code == 200
        assert client.get("/api/harness/status").status_code == 403
        assert not seen
        for username in ("viewer", "alice"):
            assert client.post("/api/auth/login", json={
                "username": username, "password": "test-password-123",
            }).status_code == 200
            assert client.get("/api/harness/status").status_code == 200
            assert client.post("/api/harness/tasks", json={"prompt": "test"}).status_code == 202
        assert seen == [users["viewer"], users["viewer"], users["alice"], users["alice"]]

        assert client.post("/api/auth/login", json={
            "username": "admin", "password": "test-password-123",
        }).status_code == 200
        cookie = client.cookies.get("seealpha_session")
        client.app.state.repository.set_impersonation(
            token_digest(cookie), users["viewer"],
        )
        assert client.get("/api/harness/status").status_code == 403
        assert seen == [users["viewer"], users["viewer"], users["alice"], users["alice"]]
