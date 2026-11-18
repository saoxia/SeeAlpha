"""Platform Harness v1 integration tests: no Docker, SDK or network calls."""
import json
from dataclasses import replace

import httpx
import pytest
from fastapi.testclient import TestClient

from api.auth.security import hash_password, token_digest
from api.config import Settings
from api.db.auth_repository import AuthRepository
from api.db.database import Database
from api.main import create_app
from api.research.harness_client import (
    HARNESS_HEADER, HarnessClient, HarnessUnavailable,
)

pytestmark = pytest.mark.usefixtures("platform_engine_bootstrap")

TOKEN = "local-development-harness-key-1234567890"
ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
BODY = {"id": ID, "status": "completed", "turn": 1,
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z", "final_response": "Safe markdown result",
        "prompt": "Secret user prompt", "error": None}


def setup(tmp_path, *, enabled=True):
    settings = Settings(
        tmp_path / "auth.db", cookie_secure=False,
        harness_enabled=enabled, harness_url="http://127.0.0.1:8777",
        harness_token=TOKEN,
    )
    db = Database(settings.database_path)
    db.initialize()
    repo = AuthRepository(db)
    repo.create_user("admin", hash_password("test-password-123"))
    repo.create_user("viewer", hash_password("test-password-123"))
    db.close()
    return settings


def login(client, username="admin"):
    response = client.post("/api/auth/login", json={
        "username": username, "password": "test-password-123",
    })
    assert response.status_code == 200


def test_disabled_harness_does_not_affect_platform(tmp_path):
    with TestClient(create_app(setup(tmp_path, enabled=False))) as client:
        assert client.get("/api/harness/status").status_code == 401
        login(client)
        assert client.get("/api/harness/status").json()["enabled"] is False
        response = client.post("/api/harness/tasks", json={"prompt": "A"})
        assert response.status_code == 503
        assert client.get("/api/health").json()["status"] == "ok"


def test_harness_auth_task_lifecycle_and_safe_artifact_download(tmp_path, monkeypatch):
    calls = []

    def respond(request):
        calls.append(request)
        assert request.url.host == "127.0.0.1"
        assert request.headers["authorization"] == "Bearer " + TOKEN
        assert request.headers[HARNESS_HEADER] == "1"
        path = request.url.path
        if path == "/health":
            payload = {"service": "seealpha-harness", "contract_version": 1,
                       "runtime_configured": True, "runtime_layout": "resident-container"}
            return httpx.Response(200, json=payload, headers={HARNESS_HEADER: "1"})
        if path == "/v1/tasks" and request.method == "POST":
            assert json.loads(request.content) == {"prompt": "Test factor hypothesis", "queue_if_busy": True}
            return httpx.Response(202, json=BODY, headers={HARNESS_HEADER: "1"})
        if path == f"/v1/tasks/{ID}/artifacts":
            return httpx.Response(200, json={"artifacts": [{
                "artifact_id": "summary-1", "filename": "summary-1.md",
                "kind": "markdown", "size": 10, "sha256": "a"*64,
            }]}, headers={HARNESS_HEADER: "1"})
        if path == f"/v1/tasks/{ID}/artifacts/summary-1":
            return httpx.Response(200, content=b"1234567890", headers={
                HARNESS_HEADER: "1", "Content-Type": "text/html",
            })
        if path == f"/v1/tasks/{ID}/cancel":
            return httpx.Response(202, json={**BODY,"status":"cancelled"},headers={HARNESS_HEADER:"1"})
        if path == f"/v1/tasks/{ID}/continue":
            assert json.loads(request.content)["prompt"] == "Try another hypothesis"
            return httpx.Response(202, json={**BODY,"turn":2,"status":"queued"},headers={HARNESS_HEADER:"1"})
        if path == f"/v1/tasks/{ID}":
            return httpx.Response(200, json=BODY,headers={HARNESS_HEADER:"1"})
        raise AssertionError(f"Unexpected request {request.method} {path}")

    from api.research import harness_router
    class MockHarness(HarnessClient):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, transport=httpx.MockTransport(respond), **kwargs)
    monkeypatch.setattr(harness_router, "HarnessClient", MockHarness)
    with TestClient(create_app(setup(tmp_path))) as client:
        assert client.get("/api/harness/status").status_code == 401
        login(client,"viewer")
        assert client.get("/api/harness/status").status_code == 403
        assert client.post("/api/harness/tasks",json={"prompt":"Test"}).status_code == 403
        login(client,"admin")
        response = client.get("/api/harness/status")
        assert response.status_code == 200 and response.json()["available"]
        response = client.post("/api/harness/tasks",json={"prompt":"Test factor hypothesis"})
        assert response.status_code == 202
        assert "prompt" not in response.json()
        assert response.json()["id"] == ID
        assert client.get(f"/api/harness/tasks/{ID}").json()["final_response"] == "Safe markdown result"
        assert client.post(f"/api/harness/tasks/{ID}/continue",json={"prompt":"Try another hypothesis"}).json()["turn"] == 2
        assert client.post(f"/api/harness/tasks/{ID}/cancel").json()["status"] == "cancelled"
        manifests = client.get(f"/api/harness/tasks/{ID}/artifacts").json()["artifacts"]
        assert manifests[0]["artifact_id"] == "summary-1"
        download = client.get(f"/api/harness/tasks/{ID}/artifacts/summary-1")
        assert download.status_code == 200 and download.content == b"1234567890"
        assert download.headers["content-type"].startswith("application/octet-stream")
        assert download.headers["x-content-type-options"] == "nosniff"
        assert download.headers["content-disposition"].startswith("attachment;")
        assert client.get(f"/api/harness/tasks/{ID}/artifacts/../../api/health").status_code in {404,422}
        assert len(calls) >= 7


def test_no_impersonated_admin_agent_execution(tmp_path, monkeypatch):
    from api.research import harness_router
    def forbidden(*args, **kwargs):
        raise AssertionError("Harness must not be contacted during impersonation")
    monkeypatch.setattr(harness_router,"HarnessClient",forbidden)
    with TestClient(create_app(setup(tmp_path))) as client:
        login(client)
        cookie = client.cookies.get("seealpha_session")
        repository = client.app.state.repository
        viewer = repository.get_user_by_username("viewer")
        repository.set_impersonation(token_digest(cookie),viewer["id"])
        assert client.get("/api/harness/status").status_code == 403
        assert client.post("/api/harness/tasks",json={"prompt":"Agent"}).status_code == 403


def test_transport_errors_do_not_disclose_secrets():
    def failed(_request):
        raise httpx.ConnectError("secret " + TOKEN)
    client = HarnessClient("http://127.0.0.1:8777",TOKEN,
                           transport=httpx.MockTransport(failed))
    try:
        with pytest.raises(HarnessUnavailable) as exc:
            client.health()
        assert TOKEN not in str(exc.value)
    finally:
        client.close()
    for origin in ("file:///tmp/x","http://evil.example/path",
                   "http://user:password@evil.example","http://host/?token=secret"):
        with pytest.raises(ValueError):
            HarnessClient(origin,TOKEN)
