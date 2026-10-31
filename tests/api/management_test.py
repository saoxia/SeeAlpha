"""Authenticated research jobs, published artifacts and request boundaries."""

import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.auth.security import hash_password
from api.config import Settings
from api.db.auth_repository import AuthRepository
from api.db.database import Database
from api.main import create_app
from api.management.jobs import CHECK_MODULE
from api.management.service import snapshot
from tests.api.auth_test import login
from tests.web.jobs_test import module


@pytest.fixture
def api(tmp_path):
    settings = Settings(tmp_path / "auth.db", cookie_secure=False)
    database = Database(settings.database_path)
    database.initialize()
    repository = AuthRepository(database)
    repository.create_user("admin", hash_password("test-password-123"))
    repository.create_user("viewer", hash_password("test-password-123"))
    with TestClient(create_app(settings, project=tmp_path)) as client:
        yield client, tmp_path


def test_management_requires_login_and_admin_to_mutate(api):
    client, _root = api
    assert client.get("/api/management/state").status_code == 401
    assert client.get("/api/management/jobs/missing").status_code == 401
    assert client.post("/api/management/jobs", json={"action": "check_quick"}).status_code == 401
    login(client, username="viewer")
    state = client.get("/api/management/state")
    assert state.status_code == 200 and state.headers["cache-control"] == "no-store"
    body = state.json()
    assert "csrf_token" not in body
    assert body["checks"]["stale"] and body["checks"]["data"] is None
    assert body["jobs"] == body["reports"] == []
    assert len(body["strategies"]) == 8 and len(body["actions"]) == 3
    assert client.post("/api/management/jobs", json={"action": "check_quick"}).status_code == 403
    assert client.post("/api/management/jobs/missing/cancel", json={}).status_code == 403
    assert body["jobs"] == []


@pytest.mark.parametrize(
    "payload",
    [
        {"action": "check_quick", "shell": "id"},
        {"action": "check_quick", "parameters": {"root": "/tmp"}},
        {
            "action": "backtest",
            "parameters": {
                "strategy": "../../api",
                "start": "2026-08-01",
                "end": "2026-08-02",
            },
        },
        [],
    ],
)
def test_invalid_job_requests_do_not_create_records(api, payload):
    client, root = api
    login(client)
    response = client.post("/api/management/jobs", json=payload)
    assert response.status_code == 400
    assert client.get("/api/management/state").json()["jobs"] == []
    jobs = root / ".local/state/web/jobs"
    assert not jobs.exists() or list(jobs.iterdir()) == []


def test_malformed_content_and_oversized_body_are_rejected(api):
    client, _root = api
    login(client)
    assert (
        client.post(
            "/api/management/jobs", content=b"{}", headers={"content-type": "text/plain"}
        ).status_code
        == 415
    )
    assert (
        client.post(
            "/api/management/jobs",
            content=b" " * 4097,
            headers={"content-type": "application/json"},
        ).status_code
        == 400
    )
    malformed = client.post(
        "/api/management/jobs", content=b"{", headers={"content-type": "application/json"}
    )
    assert malformed.status_code == 400
    assert client.post("/api/management/jobs/job-1/cancel", json={"force": True}).status_code == 400
    assert client.post("/api/management/jobs/missing/cancel", json={}).status_code == 404


def test_cross_site_post_cannot_start_a_job(api):
    client, _root = api
    login(client)
    for headers in (
        {"origin": "https://evil.example"},
        {"sec-fetch-site": "cross-site"},
    ):
        response = client.post(
            "/api/management/jobs", json={"action": "check_quick"}, headers=headers
        )
        assert response.status_code == 403
    assert client.get("/api/management/state").json()["jobs"] == []
    assert client.get("/api/management/state", headers={"host": "evil.example"}).status_code == 400


def test_running_job_conflicts_until_cancelled(api):
    client, root = api
    login(client)
    module(
        root,
        CHECK_MODULE,
        "import time\nfrom pathlib import Path\nPath('ready').touch()\ntime.sleep(30)\n",
    )
    started = client.post("/api/management/jobs", json={"action": "check_quick"})
    assert started.status_code == 202, started.text
    identity = started.json()["job"]["id"]
    deadline = time.monotonic() + 5
    while not (root / "ready").exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert (root / "ready").exists()
    assert client.post("/api/management/jobs", json={"action": "check_full"}).status_code == 409
    cancelled = client.post(f"/api/management/jobs/{identity}/cancel", json={})
    assert cancelled.status_code == 200
    assert cancelled.json()["job"]["status"] == "cancelled"
    listed = client.get("/api/management/state").json()["jobs"]
    assert listed[0]["id"] == identity and "log" not in listed[0]
    detail = client.get(f"/api/management/jobs/{identity}")
    assert detail.status_code == 200 and "log" in detail.json()


@pytest.mark.parametrize(
    "path",
    [
        "data/../secret.txt",
        "../secret.txt",
        "logs/web/checks/run/observations.sqlite",
        "logs/web/checks/run/raw/evidence.json",
    ],
)
def test_artifacts_refuse_traversal_and_private_files(api, path):
    client, root = api
    login(client)
    study = root / "logs/web/checks/run"
    study.mkdir(parents=True)
    (study / "observations.sqlite").write_text("private observation database")
    (study / "raw").mkdir()
    (study / "raw/evidence.json").write_text('{"private":"raw evidence"}')
    (root.parent / "secret.txt").write_text("private outside project")
    assert client.get("/api/management/artifacts/" + path).status_code == 404


def test_artifact_symlinks_and_external_roots_follow_published_rules(api):
    client, root = api
    login(client)
    study = root / "logs/web/checks/run"
    study.mkdir(parents=True)
    secret = root / "secret.txt"
    secret.write_text("private")
    (study / "results.json").symlink_to(secret)
    assert client.get("/api/management/artifacts/logs/web/checks/run/results.json").status_code == 404
    external = root.parent / (root.name + "-external")
    external.mkdir()
    (root / "data").symlink_to(external, target_is_directory=True)
    report = root / "data/reports/backtest/example/run"
    report.mkdir(parents=True)
    (report / "report.html").write_text("published report")
    served = client.get("/api/management/artifacts/data/reports/backtest/example/run/report.html")
    assert served.status_code == 200 and served.text == "published report"
    assert "sandbox" in served.headers["content-security-policy"]
    assert "allow-same-origin" not in served.headers["content-security-policy"]
    assert "connect-src 'none'" in served.headers["content-security-policy"]
    assert served.headers["x-content-type-options"] == "nosniff"
    assert client.head(
        "/api/management/artifacts/data/reports/backtest/example/run/report.html"
    ).content == b""
    (report / "summary.json").symlink_to(external / "secret.html")
    (external / "secret.html").write_text("private")
    summary = "/api/management/artifacts/data/reports/backtest/example/run/summary.json"
    assert client.get(summary).status_code == 404


@pytest.mark.parametrize(
    "content",
    ["not json", "[]", '{"checked_ms":{}}', '{"checked_ms":NaN}', '{"checked_ms":Infinity}'],
)
def test_invalid_snapshot_does_not_hide_the_rest_of_state(api, content):
    client, root = api
    login(client)
    checks = root / "logs/web/checks"
    checks.mkdir(parents=True)
    (checks / "latest.json").write_text(content)
    state = client.get("/api/management/state")
    assert state.status_code == 200
    body = state.json()
    assert body["checks"]["error"] and body["checks"]["stale"]
    assert body["jobs"] == body["reports"] == []


def test_old_check_snapshot_is_marked_stale(tmp_path):
    (tmp_path / "latest.json").write_text(
        json.dumps({"healthy": True, "checked_ms": (time.time() - 600) * 1000})
    )
    result = snapshot(tmp_path, "latest.json", 180)
    assert result["data"]["healthy"] is True
    assert result["stale"] is True and result["age_seconds"] >= 600


def test_published_report_is_listed_and_downloadable(api):
    client, root = api
    login(client)
    relative = Path("data/reports/backtest/example/run-1/report.html")
    report = root / relative
    report.parent.mkdir(parents=True)
    report.write_text("<!doctype html><p>Report</p>")
    (report.with_name("summary.json")).write_text("{}")
    state = client.get("/api/management/state").json()
    assert state["report_count"] == 1
    assert state["reports"][0]["url"] == "/artifacts/" + relative.as_posix()
    opened = client.get("/api/management" + state["reports"][0]["url"])
    assert opened.status_code == 200 and b"Report" in opened.content
    summary = client.get("/api/management" + state["reports"][0]["summary_url"])
    assert summary.status_code == 200 and summary.json() == {}
