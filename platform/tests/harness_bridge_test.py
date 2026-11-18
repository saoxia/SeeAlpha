"""Platform Harness v1 integration tests: no Docker, SDK or network calls."""
import json
from hashlib import sha256

import httpx
import pytest
from api.auth.security import hash_password, token_digest
from api.config import Settings
from api.db.auth_repository import AuthRepository
from api.db.database import Database
from api.main import create_app
from api.research.harness_client import (
    HARNESS_HEADER,
    HarnessClient,
    HarnessUnavailable,
)
from fastapi.testclient import TestClient

pytestmark = pytest.mark.usefixtures("platform_engine_bootstrap")

TOKEN = "local-development-harness-key-1234567890"
ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
BODY = {"id": ID, "status": "completed", "turn": 1,
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z", "final_response": "Safe markdown result",
        "prompt": "Secret user prompt", "error": None}


def setup(tmp_path, *, enabled=True, owner=""):
    settings = Settings(
        tmp_path / "auth.db", cookie_secure=False,
        harness_enabled=enabled, harness_url="http://127.0.0.1:8777",
        harness_token=TOKEN, harness_owner_username=owner,
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
        if path == "/v1/tasks" and request.method == "GET":
            assert request.url.params["limit"] == "50"
            return httpx.Response(200, json={"tasks":[{
                key: BODY.get(key) for key in
                ("id", "status", "turn", "created_at", "updated_at", "error")
            }]}, headers={HARNESS_HEADER:"1"})
        if path == f"/v1/tasks/{ID}/timeline":
            assert request.url.params["after"] == "0"
            return httpx.Response(200,json={
                "events":[{"sequence":1,"type":"progress",
                           "timestamp":"2026-01-01T00:00:00Z",
                           "payload":{"stage":"runtime_starting","model_key":"never-expose"}}],
                "next_cursor":1,"has_more":False,
            }, headers={HARNESS_HEADER:"1"})
        if path == f"/v1/tasks/{ID}/artifacts/import":
            assert json.loads(request.content) == {"source_file":"exports/report.csv"}
            return httpx.Response(201,json={
                "artifact_id":"artifact-aabb","filename":"report.csv",
                "kind":"table","size":10,"sha256":sha256(b"1234567890").hexdigest(),
            },headers={HARNESS_HEADER:"1"})
        if path == "/v1/tasks" and request.method == "POST":
            assert json.loads(request.content) == {"prompt": "Test factor hypothesis", "queue_if_busy": True}
            return httpx.Response(202, json=BODY, headers={HARNESS_HEADER: "1"})
        if path == f"/v1/tasks/{ID}/artifacts":
            return httpx.Response(200, json={"artifacts": [{
                "artifact_id": "summary-1", "filename": "summary-1.md",
                "kind": "markdown", "size": 10, "sha256": sha256(b"1234567890").hexdigest(),
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
        history = client.get("/api/harness/tasks").json()["tasks"]
        assert history[0]["id"] == ID and "prompt" not in history[0]
        events = client.get(f"/api/harness/tasks/{ID}/timeline?after=0").json()
        assert events["next_cursor"] == 1
        assert events["events"][0]["stage"] == "runtime_starting"
        assert "model_key" not in events["events"][0]
        imported = client.post(
            f"/api/harness/tasks/{ID}/artifacts/import",
            json={"source_file":"exports/report.csv"},
        )
        assert imported.status_code == 201
        assert imported.json()["artifact_id"] == "artifact-aabb"
        assert client.post(
            f"/api/harness/tasks/{ID}/artifacts/import",
            json={"source_file":"../../passwd"},
        ).status_code == 503
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


def test_named_trusted_user_can_use_harness_without_admin_privilege(tmp_path, monkeypatch):
    from api.research import harness_router

    seen = []

    def respond(request):
        seen.append((request.method, request.url.path))
        if request.url.path == "/health":
            return httpx.Response(
                200, json={"service": "seealpha-harness", "contract_version": 1,
                           "runtime_configured": True, "runtime_layout": "resident-container"},
                headers={HARNESS_HEADER: "1"},
            )
        if request.url.path == "/v1/tasks" and request.method == "POST":
            return httpx.Response(202, json=BODY, headers={HARNESS_HEADER: "1"})
        raise AssertionError("unexpected Harness request")

    class MockHarness(HarnessClient):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, transport=httpx.MockTransport(respond), **kwargs)

    monkeypatch.setattr(harness_router, "HarnessClient", MockHarness)
    with TestClient(create_app(setup(tmp_path, owner="viewer"))) as client:
        login(client, "admin")
        assert client.get("/api/harness/status").status_code == 403
        assert client.post("/api/harness/tasks", json={"prompt": "x"}).status_code == 403
        assert not seen

        login(client, "viewer")
        assert client.get("/api/harness/status").status_code == 200
        assert client.post("/api/harness/tasks", json={"prompt": "User research"}).status_code == 202
        assert seen == [("GET", "/health"), ("POST", "/v1/tasks")]

        # Admin impersonation must never be allowed to act as the owner.
        login(client, "admin")
        actor_cookie = client.cookies.get("seealpha_session")
        viewer = client.app.state.repository.get_user_by_username("viewer")
        client.app.state.repository.set_impersonation(token_digest(actor_cookie), viewer["id"])
        assert client.get("/api/harness/status").status_code == 403
        assert client.post("/api/harness/tasks", json={"prompt": "impersonate"}).status_code == 403
        assert len(seen) == 2


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
