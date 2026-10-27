"""Exercise local HTTP boundaries without launching jobs or touching runtime data."""

import base64
import http.client
import json
import threading
import time
from types import SimpleNamespace

import pytest

from web.jobs import BusyError
from web.server import Console, ConsoleServer, snapshot


class FakeJobs:
    def __init__(self):
        self.started = []
        self.cancelled = []
        self.busy = False

    def list_jobs(self):
        return []

    def get_job(self, identity):
        if identity != "job-1":
            raise KeyError(identity)
        return {"id": identity, "status": "running", "log": "test output"}

    def start(self, action, parameters):
        if self.busy:
            raise BusyError("busy")
        self.started.append((action, parameters))
        return {"id": "job-1", "status": "running"}

    def cancel(self, identity):
        self.get_job(identity)
        self.cancelled.append(identity)
        return {"id": identity, "status": "cancelled"}


@pytest.fixture
def web(tmp_path, request):
    jobs = FakeJobs()
    console = Console(tmp_path, jobs=jobs)
    network = getattr(request, "param", "127.0.0.1") != "127.0.0.1"
    server = ConsoleServer(
        ("0.0.0.0" if network else "127.0.0.1", 0),
        console,
        password="test-password-123456" if network else None,
    )
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
    thread.start()
    host = f"127.0.0.1:{server.server_address[1]}"

    def request(method, path, *, body=None, headers=None):
        fields = {"Host": host}
        if network:
            fields["Authorization"] = (
                "Basic " + base64.b64encode(b"admin:test-password-123456").decode()
            )
        if method == "POST":
            fields.update(
                {
                    "Origin": f"http://{host}",
                    "X-CSRF-Token": console.token,
                    "Content-Type": "application/json",
                }
            )
        for key, value in (headers or {}).items():
            if value is None:
                fields.pop(key, None)
            else:
                fields[key] = value
        connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=3)
        try:
            connection.request(method, path, body=body, headers=fields)
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    yield SimpleNamespace(root=tmp_path, jobs=jobs, console=console, request=request)
    server.shutdown()
    server.server_close()
    thread.join(timeout=3)
    assert not thread.is_alive()


def test_reading_state_and_job_is_read_only_with_missing_reports(web):
    status, headers, body = web.request("GET", "/api/state")
    assert status == 200 and headers["Cache-Control"] == "no-store"
    state = json.loads(body)
    assert state["csrf_token"] == web.console.token
    assert state["checks"]["data"] is None and state["checks"]["stale"]
    assert state["checks"]["error"]
    assert state["jobs"] == state["reports"] == []
    assert web.request("GET", "/api/jobs/job-1")[0] == 200
    assert web.request("GET", "/api/jobs/missing")[0] == 404
    assert web.jobs.started == web.jobs.cancelled == []
    assert list(web.root.iterdir()) == []


@pytest.mark.parametrize(
    "headers",
    [
        {"Host": "attacker.example"},
        {"Origin": "https://attacker.example"},
        {"Origin": None},
        {"X-CSRF-Token": "wrong"},
        {"X-CSRF-Token": None},
        {"Sec-Fetch-Site": "cross-site"},
    ],
)
def test_untrusted_post_cannot_start_or_cancel_jobs(web, headers):
    for path, body in (
        ("/api/jobs", '{"action":"check_quick"}'),
        ("/api/jobs/job-1/cancel", "{}"),
    ):
        assert web.request("POST", path, body=body, headers=headers)[0] == 403
    assert web.jobs.started == web.jobs.cancelled == []


@pytest.mark.parametrize(
    "headers", [{"Host": "attacker.example"}, {"Sec-Fetch-Site": "cross-site"}]
)
def test_untrusted_get_cannot_read_page_token(web, headers):
    status, _, body = web.request("GET", "/api/state", headers=headers)
    assert status == 403
    assert web.console.token.encode() not in body


def test_valid_post_passes_action_and_parameters_to_manager(web):
    parameters = {"strategy": "example", "start": "2026-08-01", "end": "2026-08-02"}
    status, _, body = web.request(
        "POST", "/api/jobs", body=json.dumps({"action": "backtest", "parameters": parameters})
    )
    assert status == 202 and json.loads(body)["job"]["id"] == "job-1"
    assert web.jobs.started == [("backtest", parameters)]
    assert web.request("POST", "/api/jobs/job-1/cancel", body="{}")[0] == 200
    assert web.jobs.cancelled == ["job-1"]


def test_busy_job_reports_conflict_without_starting_another(web):
    web.jobs.busy = True
    status, _, body = web.request("POST", "/api/jobs", body='{"action":"check_quick"}')
    assert status == 409 and json.loads(body)["error"]
    assert web.jobs.started == []


@pytest.mark.parametrize("body", ["{", "[]", "null", '{"action":"check_quick","shell":"id"}'])
def test_malformed_job_request_does_not_reach_manager(web, body):
    assert web.request("POST", "/api/jobs", body=body)[0] == 400
    assert web.jobs.started == []


def test_wrong_content_type_and_oversized_body_are_rejected(web):
    assert (
        web.request("POST", "/api/jobs", body="{}", headers={"Content-Type": "text/plain"})[0]
        == 415
    )
    assert web.request("POST", "/api/jobs", body=" " * 4097)[0] == 400
    assert web.jobs.started == []


def test_unknown_routes_and_cancel_parameters_do_not_mutate_jobs(web):
    assert web.request("POST", "/api/execute", body="{}")[0] == 404
    assert web.request("POST", "/api/jobs/job-1/cancel", body='{"force":true}')[0] == 400
    assert web.request("POST", "/api/jobs/missing/cancel", body="{}")[0] == 404
    assert web.jobs.started == web.jobs.cancelled == []


@pytest.mark.parametrize(
    "path",
    [
        "/artifacts/../secret.txt",
        "/artifacts/%2e%2e/secret.txt",
        "/artifacts/%2Fetc/passwd",
        "/artifacts/logs/web/checks/run/observations.sqlite",
        "/artifacts/logs/web/checks/run/raw/evidence.json",
    ],
)
def test_artifacts_refuse_traversal_absolute_and_private_data(web, path):
    study = web.root / "logs/web/checks/run"
    study.mkdir(parents=True)
    (study / "observations.sqlite").write_text("private observation database")
    (study / "raw").mkdir()
    (study / "raw/evidence.json").write_text('{"private":"raw evidence"}')
    (web.root.parent / "secret.txt").write_text("private outside project")
    assert web.request("GET", path)[0] == 404


def test_artifact_symlinks_are_refused_even_for_allowed_report_names(web):
    study = web.root / "logs/web/checks/run"
    study.mkdir(parents=True)
    secret = web.root / "secret.txt"
    secret.write_text("private")
    (study / "results.json").symlink_to(secret)
    assert web.request("GET", "/artifacts/logs/web/checks/run/results.json")[0] == 404
    runs = web.root / "data/reports/backtest"
    runs.mkdir(parents=True)
    outside = web.root / "private_run"
    (outside / "run-1").mkdir(parents=True)
    (outside / "run-1/report.html").write_text("private report")
    (runs / "strategy").symlink_to(outside, target_is_directory=True)
    path = "/artifacts/data/reports/backtest/strategy/run-1/report.html"
    assert web.request("GET", path)[0] == 404


def test_external_data_root_serves_published_files_but_refuses_nested_links(web):
    external = web.root.parent / (web.root.name + "-external")
    external.mkdir()
    (web.root / "data").symlink_to(external, target_is_directory=True)
    study = web.root / "data/reports/backtest/example/run"
    study.mkdir(parents=True)
    (study / "report.html").write_text("published report")
    assert web.request("GET", "/artifacts/data/reports/backtest/example/run/report.html")[0] == 200
    secret = external / "secret.html"
    secret.write_text("private")
    (study / "summary.json").symlink_to(secret)
    assert web.request("GET", "/artifacts/data/reports/backtest/example/run/summary.json")[0] == 404


def test_local_check_logs_cannot_redirect_to_external_files(web):
    external = web.root.parent / (web.root.name + "-checks")
    (external / "run").mkdir(parents=True)
    (external / "run/results.json").write_text("private")
    (web.root / "logs/web").mkdir(parents=True)
    (web.root / "logs/web/checks").symlink_to(external, target_is_directory=True)
    assert web.request("GET", "/artifacts/logs/web/checks/run/results.json")[0] == 404


@pytest.mark.parametrize(
    "relative",
    [
        "logs/web/checks/run/results.json",
        "data/reports/backtest/example/run-1/report.html",
        "data/reports/backtest/portfolio/example/run-1/report.html",
    ],
)
def test_published_reports_are_served_with_isolated_content_policy(web, relative):
    report = web.root / relative
    report.parent.mkdir(parents=True)
    report.write_text("<!doctype html><p>Report</p>")
    status, headers, body = web.request("GET", "/artifacts/" + relative)
    assert status == 200 and b"Report" in body
    policy = headers["Content-Security-Policy"]
    assert "sandbox" in policy and "allow-same-origin" not in policy
    assert "connect-src 'none'" in policy and headers["X-Content-Type-Options"] == "nosniff"
    assert web.request("HEAD", "/artifacts/" + relative)[2] == b""
    if relative.startswith("strategy_python"):
        reports = json.loads(web.request("GET", "/api/state")[2])["reports"]
        assert len(reports) == 1 and reports[0]["url"] == "/artifacts/" + relative


@pytest.mark.parametrize(
    "content",
    ["not json", "[]", '{"checked_ms":{}}', '{"checked_ms":NaN}', '{"checked_ms":Infinity}'],
)
def test_invalid_snapshot_is_reported_without_hiding_other_state(web, content):
    checks = web.root / "logs/web/checks"
    checks.mkdir(parents=True)
    (checks / "latest.json").write_text(content)
    status, _, body = web.request("GET", "/api/state")
    assert status == 200
    state = json.loads(body)
    assert state["checks"]["error"] and state["checks"]["stale"]
    assert state["jobs"] == state["reports"] == []


def test_old_healthy_snapshot_is_marked_stale(tmp_path):
    (tmp_path / "latest.json").write_text(
        json.dumps({"healthy": True, "checked_ms": (time.time() - 600) * 1000})
    )
    result = snapshot(tmp_path, "latest.json", 180)
    assert result["data"]["healthy"] is True
    assert result["stale"] is True and result["age_seconds"] >= 600


@pytest.mark.parametrize("web", ["0.0.0.0"], indirect=True)
@pytest.mark.parametrize(
    "host,scheme",
    [
        ("192.168.31.189:8765", "http"),
        ("console.example:8765", "http"),
        ("console.example", "https"),
    ],
)
def test_network_address_can_read_submit_and_cancel(web, host, scheme):
    headers = {"Host": host, "Origin": f"{scheme}://{host}"}
    assert web.request("GET", "/api/state", headers=headers)[0] == 200
    assert (
        web.request("POST", "/api/jobs", body='{"action":"check_quick"}', headers=headers)[0] == 202
    )
    assert web.request("POST", "/api/jobs/job-1/cancel", body="{}", headers=headers)[0] == 200
    assert web.jobs.started == [("check_quick", {})]
    assert web.jobs.cancelled == ["job-1"]


@pytest.mark.parametrize("web", ["0.0.0.0"], indirect=True)
@pytest.mark.parametrize(
    "extra",
    [
        {"Origin": "http://another.example"},
        {"X-CSRF-Token": "wrong"},
        {"Sec-Fetch-Site": "cross-site"},
    ],
)
def test_network_access_still_requires_same_origin_and_token(web, extra):
    headers = {"Host": "console.example", "Origin": "http://console.example", **extra}
    assert (
        web.request("POST", "/api/jobs", body='{"action":"check_quick"}', headers=headers)[0] == 403
    )
    assert not web.jobs.started


@pytest.mark.parametrize("web", ["0.0.0.0"], indirect=True)
@pytest.mark.parametrize(
    "host",
    [
        "",
        "user@console.example",
        "console.example/path",
        "console.example:bad",
        "console.example:99999",
        "console.example?query",
    ],
)
def test_network_mode_rejects_malformed_host(web, host):
    assert web.request("GET", "/api/state", headers={"Host": host})[0] == 403


@pytest.mark.parametrize("web", ["0.0.0.0"], indirect=True)
@pytest.mark.parametrize("authorization", [None, "Basic wrong"])
def test_network_authentication_protects_page_state_reports_and_actions(web, authorization):
    headers = {"Authorization": authorization}
    for path in ["/", "/api/state", "/api/jobs/job-1", "/artifacts/private"]:
        status, response_headers, body = web.request("GET", path, headers=headers)
        assert status == 401
        assert "Basic" in response_headers["WWW-Authenticate"]
        assert web.console.token.encode() not in body
    assert (
        web.request("POST", "/api/jobs", body='{"action":"check_quick"}', headers=headers)[0] == 401
    )
    assert not web.jobs.started


def test_network_server_refuses_to_run_without_password(tmp_path):
    with pytest.raises(ValueError, match="口令"):
        ConsoleServer(("0.0.0.0", 0), Console(tmp_path, jobs=FakeJobs()))
