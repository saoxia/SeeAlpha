"""Authenticated data operations, process exclusion and restart states."""

import json
import shutil
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

from api.auth.security import hash_password
from api.config import Settings
from api.db.auth_repository import auth_repository
from api.main import create_app
from api.market_data.contracts import DataJobRequest
from api.market_data.service import DataService
from engine.pipeline.models import PipelineSettings
from tests.api.auth_test import login

PARAMETERS = {"symbol": "ETHUSDT", "start": "2026-08-01T00:00:00Z",
              "end": "2026-08-02T00:00:00Z", "intervals": ["1m"]}


@pytest.fixture
def context(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'business.db'}"
    config = Config("alembic.ini")
    config.attributes["database_url"] = url
    command.upgrade(config, "head")
    app = create_app(Settings(tmp_path / "reference.db", database_url=url, cookie_secure=False))
    database = app.state.database
    database.initialize()
    repository = auth_repository(database)
    repository.create_user("admin", hash_password("test-password-123"))
    repository.create_user("viewer", hash_password("test-password-123"))
    (tmp_path / "data").mkdir()
    settings = PipelineSettings("futures_um", "trades", ("ETHUSDT",), ("1m",),
                                tmp_path / "data/raw", tmp_path / "data/catalog", tmp_path / "data/state")
    for module in ("api.market_data.router", "api.market_data.service"):
        monkeypatch.setattr(f"{module}.load_settings", lambda: settings)
    async def catalog(_environment):
        return [{"symbol": f"{base}/USDT:USDT", "base": base, "quote": "USDT", "market_type": "linear"}
                for base in ("ETH", "XRP")] + [{"symbol": "ETH/USDC:USDC", "base": "ETH", "quote": "USDC", "market_type": "linear"}]

    app.state.exchanges.catalog = catalog
    app.state.market_data = DataService(database, project=tmp_path)
    app.state.market_data.engine = app.state.engine
    with TestClient(app) as client:
        yield client, app, tmp_path


def test_auth_plan_storage_and_feed_expiry(context):
    client, _app, root = context
    assert client.get("/api/data/status").status_code == 401
    login(client, username="viewer")
    assert not client.get("/api/data/capabilities").json()["can_operate"]
    assert client.post("/api/data/jobs", json={**PARAMETERS, "action": "prepare"}).status_code == 403
    login(client)
    plan = client.post("/api/data/prepare/plan", json=PARAMETERS)
    assert plan.status_code == 200, plan.text
    assert plan.json()["missing_days"] == 1
    assert not (root / "data/catalog").exists()
    assert client.get("/api/data/feed/status").json()["state"] == "STOPPED"
    status_path = root / "data/market_data/binance_um_1m/status.json"
    status_path.parent.mkdir(parents=True)
    status_path.write_text(json.dumps({"state": "READY", "data_ready": True,
                                       "valid_until_ms": 1, "checked_ms": 0}))
    feed = client.get("/api/data/feed/status").json()
    assert feed["state"] == "DEGRADED" and not feed["data_ready"]
    assert client.post("/api/data/jobs", json={**PARAMETERS, "action": "prepare", "force": True}).status_code == 422
    assert client.post("/api/data/jobs", json={**PARAMETERS, "action": "prepare"},
                       headers={"origin": "https://evil.example"}).status_code == 403
    assert client.post("/api/data/jobs", json={**PARAMETERS, "action": "delete"}).status_code == 422
    unsupported = {"symbol": "UNKNOWNUSDT", "start": PARAMETERS["start"], "end": PARAMETERS["end"]}
    assert client.get("/api/data/quality", params=unsupported).json()["status"] == "unverified"
    naive = {**PARAMETERS, "start": "2026-08-01T00:00:00"}
    assert client.post("/api/data/prepare/plan", json=naive).status_code == 422
    assert client.get("/api/data/jobs/../../private").status_code == 404
    # A missing mount must not be presented as an empty dataset or recreated.
    import shutil
    shutil.rmtree(root / "data")
    assert not client.get("/api/data/status").json()["storage"]["available"]
    assert client.post("/api/data/jobs", json={**PARAMETERS, "action": "prepare"}).status_code == 503
    assert not (root / "data").exists()


def test_tick_only_plan_and_inventory_levels(context):
    client, _app, _root = context
    login(client)
    response = client.post("/api/data/prepare/plan", json={**PARAMETERS, "intervals": []})
    assert response.status_code == 200, response.text
    assert response.json()["request"]["intervals"] == []
    assert client.get("/api/data/datasets?interval=1m").json()["items"] == []
    assert client.get("/api/data/datasets").json()["items"] == []
    assert client.get("/api/data/datasets?interval=unsupported").status_code == 422
    assert client.post("/api/data/jobs", json={**PARAMETERS, "intervals": [], "action": "materialize"}).status_code == 422
    items = client.get("/api/data/datasets?all_levels=true").json()["items"]
    assert items == []


@pytest.mark.parametrize("product,symbol", [("futures_um", "ETHUSDT"), ("options", "BTC-261225-85000-C")])
def test_cross_year_plan_and_job_preserve_full_range(context, monkeypatch, product, symbol):
    client, app, root = context
    login(client)

    async def catalog(_environment, product="futures_um"):
        return [{"symbol": symbol, "base": "ETH", "quote": "USDT"}]

    monkeypatch.setattr(app.state.exchanges, "catalog", catalog)
    payload = {"symbol": symbol, "product": product, "start": "2024-01-01T00:00:00Z",
               "end": "2026-08-01T00:00:00Z", "intervals": ["1m"]}
    assert client.get("/api/data/capabilities").json()["max_range_days"] is None
    response = client.post("/api/data/prepare/plan", json=payload)
    assert response.status_code == 200, response.text
    plan = response.json()
    assert len(plan["days"]) == (datetime(2026, 8, 1) - datetime(2024, 1, 1)).days
    assert plan["days"][0]["day"] == "2024-01-01" and plan["days"][-1]["day"] == "2026-07-31"
    original, captured = subprocess.Popen, []

    def worker(command, **kwargs):
        captured.append(json.loads(command[command.index("--request") + 1]))
        return original([sys.executable, "-c", "print('range accepted', flush=True)"], **kwargs)

    monkeypatch.setattr("engine.supervisor.subprocess.Popen", worker)
    response = client.post("/api/data/jobs", json={**payload, "action": "prepare"})
    assert response.status_code == 202, response.text
    for thread in app.state.market_data.threads:
        thread.join(timeout=5)
    assert captured[0]["start"].startswith("2024-01-01") and captured[0]["end"].startswith("2026-08-01")
    assert captured[0]["product"] == product
    assert not (root / "data/raw").exists()


def test_feed_symbol_progress_is_read_only_validated_and_expires(context):
    client, _app, root = context
    login(client)
    path = root / "data/market_data/binance_um_1m/status.json"
    path.parent.mkdir(parents=True)
    now = int(time.time() * 1000)
    value = {"state": "READY", "data_ready": True, "reason": None,
             "checked_ms": now, "valid_until_ms": now + 60000,
             "symbols": {"ETHUSDT": {"next_ms": now - 1000, "last_websocket_ms": now - 10,
                                       "private_internal": "not-published"}}}
    path.write_text(json.dumps(value))
    response = client.get("/api/data/feed/status").json()
    assert response["data_ready"] and response["state"] == "READY"
    assert response["symbols"] == {"ETHUSDT": {"next_ms": now - 1000, "last_websocket_ms": now - 10}}
    assert json.loads(path.read_text()) == value
    value["valid_until_ms"] = 1
    path.write_text(json.dumps(value))
    response = client.get("/api/data/feed/status").json()
    assert response["reason"] == "status_expired" and not response["data_ready"]
    assert "ETHUSDT" in response["symbols"]
    value["symbols"]["ETHUSDT"]["next_ms"] = "invalid"
    path.write_text(json.dumps(value))
    response = client.get("/api/data/feed/status").json()
    assert response["reason"] == "invalid_status" and not response["data_ready"]


def test_delete_requires_admin_explicit_confirmation_and_unchanged_plan(context, monkeypatch):
    client, app, root = context
    from engine.pipeline.sync_binance_batch import archive_path
    archive = root / "data/raw" / archive_path("ETHUSDT", "trades", datetime(2026, 8, 1).date()).removeprefix("data/")
    archive.parent.mkdir(parents=True)
    archive.write_bytes(b"local-archive")
    payload = {**PARAMETERS, "delete_archives": True}
    login(client, username="viewer")
    assert client.post("/api/data/delete/plan", json=payload).status_code == 403
    login(client)
    response = client.post("/api/data/delete/plan", json=payload)
    assert response.status_code == 200, response.text
    plan = response.json()
    assert plan["file_count"] == 1 and archive.exists()
    job = {**payload, "action": "delete", "deletion_snapshot": plan["snapshot"], "confirmation": "DELETE ETHUSDT"}
    assert client.post("/api/data/jobs", json={**job, "confirmation": "DELETE BTCUSDT"}).status_code == 422
    assert client.post("/api/data/jobs", json={**job, "confirmation": None}).status_code == 422
    assert client.post("/api/data/jobs", json=job, headers={"origin": "https://evil.example"}).status_code == 403
    archive.write_bytes(b"changed-archive")
    assert client.post("/api/data/jobs", json=job).status_code == 409
    assert archive.exists() and not app.state.market_data.jobs()
    job["deletion_snapshot"] = client.post("/api/data/delete/plan", json=payload).json()["snapshot"]
    original = subprocess.Popen

    def local_worker(command, **kwargs):
        parameters = json.loads(command[command.index("--request") + 1])
        identity = command[-1]
        settings = root / "data"
        result_path = root / ".local/state/api/data_jobs" / f"{identity}.json"
        project = str(Path(__file__).resolve().parents[2])
        script = f"""
import sys, json
sys.path.insert(0, {project!r})
from pathlib import Path
from datetime import datetime
from engine.pipeline.models import PipelineSettings
from engine.pipeline.data_management import checked_request, execute
parameters = {parameters!r}
root = Path({str(settings)!r})
settings = PipelineSettings('futures_um', 'trades', ('ETHUSDT',), ('1m',), root/'raw', root/'catalog', root/'state')
request = checked_request(parameters['symbol'], datetime.fromisoformat(parameters['start']), datetime.fromisoformat(parameters['end']), parameters['intervals'], settings)
result = execute('delete', request, settings, root/'unused', expected_snapshot=parameters['deletion_snapshot'], delete_archives=True)
Path({str(result_path)!r}).write_text(json.dumps(result))
"""
        return original([sys.executable, "-c", script], **kwargs)

    monkeypatch.setattr("engine.supervisor.subprocess.Popen", local_worker)
    response = client.post("/api/data/jobs", json=job)
    assert response.status_code == 202, response.text
    for thread in app.state.market_data.threads:
        thread.join(timeout=15)
    detail = client.get(f"/api/data/jobs/{response.json()['id']}").json()
    assert detail["status"] == "succeeded", detail
    assert detail["result"]["deleted_files"] == 1
    assert not archive.exists()


def test_process_job_exclusion_cancellation_and_recovery(context, monkeypatch):
    client, app, root = context
    login(client)
    manager = app.state.market_data
    original = subprocess.Popen

    def local_worker(command, **kwargs):
        assert command[1:3] == ["-m", "engine.pipeline.data_management"]
        assert "--force" not in command
        script = "import time; print('checking', flush=True); time.sleep(30)"
        return original([sys.executable, "-c", script], **kwargs)

    monkeypatch.setattr("engine.supervisor.subprocess.Popen", local_worker)
    response = client.post("/api/data/jobs", json={**PARAMETERS, "action": "validate"})
    assert response.status_code == 202, response.text
    identity = response.json()["id"]
    assert client.post("/api/data/jobs", json={**PARAMETERS, "action": "prepare"}).status_code == 409
    other = DataService(app.state.database, project=root)
    with pytest.raises(Exception) as error:
        other.start(DataJobRequest(**PARAMETERS, action="export"))
    assert error.value.status_code == 409
    assert client.post(f"/api/data/jobs/{identity}/cancel").status_code == 200
    for thread in manager.threads:
        thread.join(timeout=5)
    assert client.get(f"/api/data/jobs/{identity}").json()["status"] == "cancelled"
    assert client.get(f"/api/data/jobs/{identity}/download").status_code == 409
    orphan = {"id": "f" * 32, "action": "tick_data:prepare", "status": "running",
              "created_at": datetime.now(UTC).isoformat(), "finished_at": None,
              "parameters": PARAMETERS, "result": None, "error": None}
    manager.repository.save_job(orphan)
    other.recover()
    assert other.job(orphan["id"])["status"] == "interrupted"


def test_success_result_and_export_download(context, monkeypatch):
    client, app, root = context
    login(client)
    original = subprocess.Popen

    def local_worker(command, **kwargs):
        identity = command[-1]
        path = root / ".local/state/api/data_jobs" / f"{identity}.json"
        output = root / "data/exports/ticks" / f"{identity}.csv"
        script = ("from pathlib import Path; import json; "
                  f"p=Path({str(path)!r}); p.write_text(json.dumps({{'rows': 2}})); "
                  f"p=Path({str(output)!r}); p.parent.mkdir(parents=True, exist_ok=True); "
                  "p.write_text('trade_id,price\\n1,3000.10\\n')")
        return original([sys.executable, "-c", script], **kwargs)

    monkeypatch.setattr("engine.supervisor.subprocess.Popen", local_worker)
    identity = client.post("/api/data/jobs", json={**PARAMETERS, "action": "export"}).json()["id"]
    for thread in app.state.market_data.threads:
        thread.join(timeout=5)
    job = client.get(f"/api/data/jobs/{identity}").json()
    assert job["status"] == "succeeded", job
    assert job["result"]["rows"] == 2
    response = client.get(f"/api/data/jobs/{identity}/download")
    assert response.status_code == 200 and "3000.10" in response.text
    output = root / "data/exports/ticks" / f"{identity}.csv"
    output.unlink()
    output.symlink_to(root / "business.db")
    assert client.get(f"/api/data/jobs/{identity}/download").status_code == 404


def test_task_stream_auth_origin_and_read_only(context):
    from starlette.testclient import WebSocketDenialResponse
    from starlette.websockets import WebSocketDisconnect

    client, _app, _root = context
    path = "/api/data/jobs/stream"
    with (pytest.raises(WebSocketDenialResponse) as denied,
          client.websocket_connect(path, headers={"origin": "http://testserver"})):
        pass
    assert denied.value.status_code == 401
    login(client, username="viewer")
    with (pytest.raises(WebSocketDenialResponse) as denied,
          client.websocket_connect(path, headers={"origin": "http://testserver"})):
        pass
    assert denied.value.status_code == 403
    login(client)
    for headers in ({}, {"origin": "https://evil.example"},
                    {"origin": "http://testserver", "sec-fetch-site": "cross-site"}):
        with (pytest.raises(WebSocketDisconnect) as denied,
              client.websocket_connect(path, headers=headers)):
            pass
        assert denied.value.code == 1008
    with client.websocket_connect(path, headers={"origin": "http://testserver"}) as ws:
        assert ws.receive_json() == {"type": "tasks", "tasks": [], "detail": None, "subscriptions": []}
        ws.send_json({"action": "prepare"})
        with pytest.raises(WebSocketDisconnect) as denied:
            ws.receive_json()
        assert denied.value.code == 1008


def test_task_stream_logs_completion_reconnect_and_revocation(context):
    from starlette.websockets import WebSocketDisconnect

    client, app, root = context
    login(client)
    manager = app.state.market_data
    identity = "a" * 32
    record = {"id": identity, "action": "tick_data:prepare", "status": "succeeded",
              "created_at": "2026-08-02T00:00:00+00:00", "finished_at": "2026-08-02T00:01:00+00:00",
              "parameters": PARAMETERS, "error": None, "result": {"ready": True}}
    manager.repository.save_job(record)
    manager.log_directory.mkdir(parents=True)
    log = manager.log_directory / f"{identity}.log"
    log.write_text("x" * 40000 + "\n转换中\n")
    path = f"/api/data/jobs/stream?job_id={identity}"
    headers = {"origin": "http://testserver"}
    with client.websocket_connect(path, headers=headers) as ws:
        first = ws.receive_json()
        assert first["detail"]["log_truncated"]
        assert first["tasks"][0]["progress"] == "转换中"
        assert len(first["detail"]["log"].encode()) <= 32768
        log.write_text("转换完成\n")
        update = ws.receive_json()
        assert update["detail"]["log"] == "转换完成\n"
        assert ws.receive_json() == {"type": "heartbeat"}
        record.update(status="failed", error="fixture failure")
        manager.repository.save_job(record)
        assert ws.receive_json()["tasks"][0]["status"] == "failed"
    with client.websocket_connect(path, headers=headers) as ws:
        assert ws.receive_json()["tasks"][0]["status"] == "failed"
        assert client.post("/api/auth/logout").status_code == 204
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_json()
        assert closed.value.code == 1008
    assert not (root / "data/catalog").exists()


def test_task_stream_storage_failure_allows_client_retry(context, monkeypatch):
    from starlette.websockets import WebSocketDisconnect

    from api.db.errors import StorageUnavailable

    client, app, _root = context
    login(client)

    def unavailable(_selected):
        raise StorageUnavailable("fixture unavailable")

    with monkeypatch.context() as patch:
        patch.setattr(app.state.market_data, "task_snapshot", unavailable)
        with client.websocket_connect("/api/data/jobs/stream", headers={"origin": "http://testserver"}) as ws:
            with pytest.raises(WebSocketDisconnect) as closed:
                ws.receive_json()
            assert closed.value.code == 1013
    with client.websocket_connect("/api/data/jobs/stream", headers={"origin": "http://testserver"}) as ws:
        assert ws.receive_json()["tasks"] == []


def test_binance_selection_has_no_presets_and_rejects_unavailable_markets(context, monkeypatch):
    from engine.connectors.binance.queries import QueryError

    client, app, _root = context
    assert client.get("/api/data/markets").status_code == 401
    login(client, username="viewer")
    assert client.get("/api/data/markets").status_code == 403
    login(client)
    assert client.get("/api/data/capabilities").json()["symbols"] == []
    assert [item["symbol"] for item in client.get("/api/data/markets").json()] == ["ETHUSDT", "XRPUSDT", "ETHUSDC"]
    payload = {**PARAMETERS, "symbol": "XRPUSDT"}
    assert client.post("/api/data/prepare/plan", json=payload).status_code == 200
    for path, body in (("prepare/plan", {**payload, "symbol": "FAKEUSDT"}),
                       ("jobs", {**payload, "symbol": "FAKEUSDT", "action": "prepare"})):
        assert client.post(f"/api/data/{path}", json=body).status_code == 422
    started = []

    def start(request):
        started.append(request.symbol)
        return {"id": "f" * 32, "action": request.action, "status": "running",
                "created_at": "2026-08-02T00:00:00Z", "finished_at": None,
                "parameters": request.model_dump(mode="json"), "error": None, "result": None}

    monkeypatch.setattr(app.state.market_data, "start", start)
    assert client.post("/api/data/jobs", json={**payload, "action": "prepare"}).status_code == 202
    assert started == ["XRPUSDT"]

    async def unavailable(_environment):
        raise QueryError("Binance 暂不可用")

    monkeypatch.setattr(app.state.exchanges, "catalog", unavailable)
    assert client.get("/api/data/markets").status_code == 502
    assert client.post("/api/data/jobs", json={**payload, "action": "prepare"}).status_code == 502
    assert started == ["XRPUSDT"]


def test_usdc_market_plans_and_unsupported_products_are_explicit(context):
    client, _app, _root = context
    login(client)
    response = client.get("/api/data/markets?product=futures_um")
    assert response.status_code == 200
    assert any(item["symbol"] == "ETHUSDC" for item in response.json())
    assert client.get("/api/data/markets?product=spot").status_code == 422
    payload = {**PARAMETERS, "symbol": "ETH-USDC"}
    result = client.post("/api/data/prepare/plan", json=payload)
    assert result.status_code == 200, result.text
    assert result.json()["request"]["symbol"] == "ETHUSDC"


def test_option_catalog_plans_payload_and_existing_data_reads(context, monkeypatch):
    from datetime import timedelta

    from engine.connectors.binance import options
    from engine.pipeline.catalog import ParquetDataCatalog
    from engine.pipeline.options_data import prepare
    from tests.connectors.binance.options_queries_test import definition
    from tests.pipeline.options_data_test import bars_request, fake_klines

    client, app, _root = context
    metadata = definition(monkeypatch)
    old_catalog = app.state.exchanges.catalog

    async def catalog(env, product="futures_um"):
        return [metadata] if product == "options" else await old_catalog(env)

    monkeypatch.setattr(app.state.exchanges, "catalog", catalog)
    login(client, username="viewer")
    assert client.get("/api/data/markets?product=options").status_code == 403
    login(client)
    response = client.get("/api/data/markets?product=options")
    assert response.status_code == 200, response.text
    assert response.json()[0]["expiry"] == metadata["expiry"]
    assert response.json()[0]["product"] == "options"
    payload = {**PARAMETERS, "symbol": metadata["symbol"], "product": "options"}
    assert client.post("/api/data/prepare/plan", json=payload).status_code == 200
    assert client.post("/api/data/prepare/plan", json={**payload, "intervals": []}).status_code == 422
    assert client.post("/api/data/jobs", json={**payload, "action": "materialize"}).status_code == 422
    assert client.post("/api/data/jobs", json={**payload, "action": "export"}).status_code == 422
    request, calls = bars_request(), []
    settings = app.state.market_data.request(DataJobRequest(**payload, action="prepare"))[1]
    monkeypatch.setattr(options, "markets", lambda: [metadata])
    fake_klines(monkeypatch, request, calls)
    assert prepare(request, settings)["ready"]
    assert ParquetDataCatalog(settings.catalog_dir).instruments()
    rows = client.get("/api/data/datasets?all_levels=true").json()["items"]
    assert rows[0]["product"] == "options"
    assert rows[0]["quality"] == "exchange_klines"

    async def unavailable(*args, **kwargs):
        raise AssertionError("existing local reads must not query the exchange")

    monkeypatch.setattr(app.state.exchanges, "catalog", unavailable)
    query = {"symbol": metadata["symbol"], "product": "options", "interval": "1m",
             "start": request.start.isoformat(), "end": (request.start + timedelta(minutes=2)).isoformat()}
    response = client.get("/api/data/bars", params=query)
    assert response.status_code == 200, response.text
    assert len(response.json()["records"]) == 2
    assert client.get("/api/data/bars", params={**query, "product": "futures_um"}).status_code == 422
    plan = client.post("/api/data/delete/plan", json={**payload, "delete_archives": True})
    assert plan.status_code == 200 and plan.json()["file_count"] == 2


def test_task_stream_does_not_repeat_durable_recovery_checks(context, monkeypatch):
    client, app, _root = context
    login(client)

    def recover():
        raise AssertionError("stream publishing must not mutate or repeat recovery")

    with monkeypatch.context() as patch:
        patch.setattr(app.state.market_data, "recover", recover)
        with client.websocket_connect("/api/data/jobs/stream", headers={"origin": "http://testserver"}) as ws:
            assert ws.receive_json()["type"] == "tasks"
            assert ws.receive_json()["type"] == "heartbeat"


def test_subscription_configuration_auth_validation_and_missing_mount_stop(context):
    client, app, root = context
    assert client.get("/api/data/subscriptions").status_code == 401
    login(client, username="viewer")
    payload = {"symbol": "ETHUSDT", "base_level": "tick", "intervals": ["1m", "5m"], "realtime_enabled": False}
    assert client.post("/api/data/subscriptions", json=payload).status_code == 403
    login(client)
    assert client.get("/api/data/subscriptions").json() == {"items": []}
    assert client.post("/api/data/subscriptions", json=payload, headers={"origin": "https://evil.example"}).status_code == 403
    response = client.post("/api/data/subscriptions", json=payload)
    assert response.status_code == 200, response.text
    assert not response.json()["policy"]["realtime_enabled"]
    assert not app.state.market_data.subscriptions.processes
    assert not (root / "data/catalog").exists()
    canonical = client.post("/api/data/subscriptions", json={**payload, "base_level": "1m", "intervals": ["1s"]})
    assert canonical.status_code == 200
    from core.market.instruments import INTERVALS
    assert canonical.json()["policy"]["intervals"] == list(INTERVALS)
    legacy_second = client.post("/api/data/subscriptions", json={**payload, "base_level": "1s"})
    assert legacy_second.status_code == 200 and legacy_second.json()["policy"]["base_level"] == "1m"
    assert legacy_second.json()["policy"]["intervals"] == list(INTERVALS)
    assert client.post("/api/data/subscriptions", json={**payload, "symbol": "../private"}).status_code == 422
    # Stopping/configuring history must remain possible while the disk is missing.
    shutil.rmtree(root / "data")
    assert client.post("/api/data/subscriptions", json=payload).status_code == 200
    assert not (root / "data").exists()


def test_subscription_process_restore_stop_expiry_and_delete_protection(context, monkeypatch):
    client, app, root = context
    login(client)
    commands, stopped = [], []

    class Process:
        def poll(self):
            return None

    def spawn(command, **kwargs):
        commands.append((command, kwargs))
        return Process()

    monkeypatch.setattr("engine.supervisor.subprocess.Popen", spawn)
    monkeypatch.setattr(app.state.market_data, "_stop", stopped.append)
    payload = {"symbol": "ETHUSDT", "base_level": "1m", "intervals": ["5m", "15m"], "realtime_enabled": True}
    response = client.post("/api/data/subscriptions", json=payload)
    assert response.status_code == 200, response.text
    assert commands[0][0][2] == "engine.feed.managed"
    saved = app.state.market_data.policy_repository.list()
    assert saved and saved[0]["symbol"] == "ETHUSDT"
    runtime_rows = app.state.engine.subscriptions(tenant_id=1)
    assert any(row["symbol"] == "ETHUSDT" and row["account_id"] == app.state.engine.market_account_id
               for row in runtime_rows)
    assert commands[0][1]["pass_fds"] and "--catalog" in commands[0][0]
    assert not (root / "data/market_data").exists()  # Fake worker never touches market data.
    path = root / "data/market_data/binance_um_1m/managed/ETHUSDT/1m/status.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"state": "READY", "data_ready": True, "reason": "continuous_closed_minutes",
                                "valid_until_ms": 1, "bar_updates": [{"is_closed": False}]}))
    state = client.get("/api/data/subscriptions").json()["items"][0]["runtime"]
    assert state["state"] == "DEGRADED" and not state["data_ready"] and not state["bar_updates"]
    # Active readers must be stopped before deleting the history they import.
    deletion = {**PARAMETERS, "action": "delete", "deletion_snapshot": "0" * 64, "confirmation": "DELETE ETHUSDT"}
    assert client.post("/api/data/jobs", json=deletion).status_code == 409
    first = app.state.market_data.subscriptions
    first.close()
    assert len(stopped) == 2
    from api.market_data.subscriptions import Subscriptions
    restored = Subscriptions(app.state.market_data)
    app.state.market_data.subscriptions = restored
    restored.restore()
    assert len(commands) == 4
    assert client.post("/api/data/subscriptions", json={**payload, "realtime_enabled": False}).status_code == 200
    assert len(stopped) == 4 and not restored.processes and not restored.auxiliary
    assert client.get("/api/data/subscriptions").json()["items"][0]["runtime"]["reason"] == "disabled"


def test_invalid_database_configuration_is_preserved_and_control_has_single_owner(context):
    client, app, _root = context
    login(client)
    payload = {"symbol": "ETHUSDT", "base_level": "1m", "realtime_enabled": False}
    assert client.post("/api/data/subscriptions", json=payload).status_code == 200
    from api.market_data.subscriptions import Subscriptions
    other = Subscriptions(app.state.market_data)
    from fastapi import HTTPException

    from api.market_data.contracts import DataPolicy
    with pytest.raises(HTTPException) as error:
        other.save(DataPolicy(**payload))
    assert error.value.status_code == 409
    from api.db.models import MarketDataPolicy
    with app.state.database.session(write=True) as session:
        row = session.get(MarketDataPolicy, (1, "futures_um", "ETHUSDT"))
        row.parameters = {**payload, "base_level": "unknown"}
    app.state.market_data.subscriptions.policies = None
    assert client.get("/api/data/subscriptions").status_code == 503
    assert client.post("/api/data/subscriptions", json=payload).status_code == 503
    with app.state.database.session() as session:
        assert session.get(MarketDataPolicy, (1, "futures_um", "ETHUSDT")).parameters["base_level"] == "unknown"


def test_legacy_auto_save_uses_all_official_periods_and_explicit_runtime(context, monkeypatch):
    client, app, _root = context
    login(client)
    commands = []

    class Process:
        def poll(self):
            return None

    def spawn(command, **_kwargs):
        commands.append(command)
        return Process()

    monkeypatch.setattr("engine.supervisor.subprocess.Popen", spawn)
    monkeypatch.setattr(app.state.market_data, "_stop", lambda _process: None)
    payload = {"symbol": "ETHUSDT", "base_level": "auto", "intervals": ["5m", "15m"], "realtime_enabled": True}
    response = client.post("/api/data/subscriptions", json=payload)
    assert response.status_code == 200, response.text
    assert response.json()["policy"]["base_level"] == "1m"
    from core.market.instruments import INTERVALS
    runtime_policy = json.loads(commands[0][commands[0].index("--policy") + 1])
    assert runtime_policy["intervals"] == list(INTERVALS)
    app.state.market_data.subscriptions._read()  # Saved explicit policies also validate on restart.
    assert client.post("/api/data/subscriptions", json={**payload, "intervals": ["1s"]}).status_code == 422
    assert client.post("/api/data/subscriptions", json={**payload, "intervals": ["1s"], "realtime_enabled": False}).status_code == 200


def test_pair_defaults_history_modes_and_raw_minimum_configuration(context, monkeypatch):
    client, app, _root = context
    login(client)
    commands = []

    class Process:
        def poll(self):
            return None

    monkeypatch.setattr("engine.supervisor.subprocess.Popen", lambda command, **kwargs: commands.append(command) or Process())
    monkeypatch.setattr(app.state.market_data, "_stop", lambda process: None)
    response = client.post("/api/data/subscriptions", json={"symbol": "ETHUSDT", "base_level": "tick", "intervals": ["1s", "30m", "1h", "1M"]})
    assert response.status_code == 200, response.text
    assert response.json()["policy"]["realtime_enabled"]
    assert "--policy" in commands[0]
    assert json.loads(commands[0][commands[0].index("--policy") + 1])["base_level"] == "tick"
    capabilities = client.get("/api/data/capabilities").json()
    assert "1M" in capabilities["intervals"] and capabilities["realtime_ticks"]
    response = client.post("/api/data/subscriptions", json={"symbol": "ETHUSDT", "base_level": "1h", "intervals": ["5m"]})
    assert response.status_code == 200
    assert len(response.json()["policy"]["intervals"]) == 16 and "1s" in response.json()["policy"]["intervals"]
    assert client.post("/api/data/subscriptions", json={"symbol": "ETHUSDT", "history_enabled": True, "history_mode": "start"}).status_code == 422
    assert client.post("/api/data/subscriptions", json={"symbol": "ETHUSDT", "history_enabled": True, "history_mode": "duration", "history_days": 0}).status_code == 422
    captured = []
    async def earliest(self, endpoint, **params):
        return [[int(datetime(2020, 1, 1, 8, tzinfo=UTC).timestamp() * 1000)]]
    monkeypatch.setattr("engine.connectors.binance.pair_data.PairREST.get", earliest)
    monkeypatch.setattr(app.state.market_data, "start", lambda payload: captured.append(payload) or {"id": "a" * 32, "status": "running"})
    for mode in ("all", "start", "duration"):
        response = client.post("/api/data/subscriptions", json={"symbol": "ETHUSDT", "base_level": "1h", "realtime_enabled": False, "history_enabled": True,
            "history_mode": mode, "history_start": "2026-08-01T00:00:00Z", "history_days": 90})
        assert response.status_code == 200, response.text
        assert "history_job" not in response.json() and not captured
        saved = response.json()["policy"]
        assert saved["history_mode"] == "start" and saved["history_days"] == 30
        expected = {"all": datetime(2020, 1, 1, tzinfo=UTC), "start": datetime(2026, 8, 1, tzinfo=UTC),
                    "duration": datetime(2026, 8, 1, tzinfo=UTC) - timedelta(days=90)}[mode]
        assert datetime.fromisoformat(saved["history_start"]) == expected
        stored = app.state.market_data.policy_repository.list()[0]
        assert stored["history_mode"] == "start" and stored["history_start"] == saved["history_start"]
        assert any(command[2] == "engine.pipeline.pair_data" and "--service" in command for command in commands)


def test_legacy_policy_file_does_not_enable_collection_by_default(context):
    client, app, _root = context
    login(client)
    path = app.state.market_data.project / ".local/state/api/data_policies.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('[{"symbol":"ETHUSDT","intervals":["5m"]}]')
    assert client.get("/api/data/subscriptions").json()["items"] == []
    assert path.read_text() == '[{"symbol":"ETHUSDT","intervals":["5m"]}]'
    assert not app.state.market_data.subscriptions.processes


def test_history_retry_preserves_resolved_bounds_and_auth(context, monkeypatch):
    client, app, _root = context
    login(client)
    manager = app.state.market_data
    parameters = {"symbol": "ETHUSDT", "product": "futures_um", "base_level": "1m", "intervals": [],
                  "realtime_enabled": False, "history_enabled": True, "history_mode": "duration", "history_days": 30,
                  "resolved_start": "2026-08-01T00:00:00+00:00", "resolved_end": "2026-09-01T00:00:00+00:00"}
    identity = "b" * 32
    manager.repository.save_job({"id": identity, "action": "tick_data:backfill", "status": "failed", "created_at": "2026-10-01T00:00:00Z",
                                 "finished_at": "2026-10-01T01:00:00Z", "parameters": parameters, "error": "network", "result": None})
    captured = []
    monkeypatch.setattr(manager, "start", lambda payload, **kwargs: captured.append(kwargs) or {"id": "c" * 32})
    manager.retry(identity)
    assert captured[0]["resolved_history"] == {key: parameters[key] for key in ("resolved_start", "resolved_end")}
    login(client, username="viewer")
    assert client.post(f"/api/data/jobs/{identity}/retry").status_code == 403


def test_managed_inventory_and_preview_read_persisted_evidence(context):
    from api.market_data.contracts import DataPolicy
    from engine.feed.pair_store import PairStore

    client, app, _root = context
    login(client)
    policy = DataPolicy(symbol="ETHUSDT", base_level="1h", realtime_enabled=False)
    app.state.market_data.subscriptions.save(policy)
    root = app.state.market_data.subscriptions.root(policy)
    root.mkdir(parents=True)
    stamp = int(datetime(2026, 8, 1, tzinfo=UTC).timestamp() * 1000)
    store = PairStore(root / "collection.sqlite", "ETHUSDT", "1h")
    store.candle("1h", [stamp, "10", "12", "9", "11", "2", stamp + 3600000 - 1], stamp + 3600000)
    store.close()
    items = client.get("/api/data/datasets?all_levels=true").json()["items"]
    assert items[0]["level"] == "1h" and items[0]["event_ranges_ns"]
    response = client.get("/api/data/bars", params={"symbol": "ETHUSDT", "start": "2026-08-01T00:45:00Z", "end": "2026-08-01T01:00:00Z", "interval": "1h"})
    assert response.status_code == 200, response.text
    assert response.json()["records"][0]["close"] == "11"
    app.state.market_data.subscriptions.save(DataPolicy(symbol="ETHUSDT", base_level="tick", realtime_enabled=False))
    retained = client.get("/api/data/datasets?all_levels=true").json()["items"]
    assert any(item["level"] == "1h" for item in retained)
    assert (root / "collection.sqlite").is_file()


@pytest.mark.parametrize("worker_succeeds,has_data", [(True, True), (False, True), (True, False)])
def test_pair_delete_stops_collection_and_removes_configuration_only_on_success(context, monkeypatch, worker_succeeds, has_data):
    client, app, root = context
    from api.market_data.contracts import DataPolicy
    from engine.pipeline.pair_deletion import delete_pair_data
    manager = app.state.market_data
    payload = {"symbol": "ETHUSDT", "product": "futures_um"}
    assert client.post("/api/data/pairs/delete/plan", json=payload).status_code == 401
    login(client, username="viewer")
    assert client.post("/api/data/pairs/delete/plan", json=payload).status_code == 403
    login(client)
    manager.subscriptions.save(DataPolicy(**payload, realtime_enabled=False))
    manager.subscriptions.save(DataPolicy(symbol="BTCUSDT", realtime_enabled=False))
    # Simulate an owned collector without starting any venue connection.
    policy = manager.subscriptions._read()[0]
    manager.subscriptions._write([policy.model_copy(update={"realtime_enabled": True}), manager.subscriptions._read()[1]])
    collector = object()
    manager.subscriptions.processes[("futures_um", "ETHUSDT")] = collector
    stopped = []
    monkeypatch.setattr(manager, "_stop", lambda process: stopped.append(process))
    archive = root / "data/raw/futures/um/daily/trades/ETHUSDT/archive.zip"
    archive.parent.mkdir(parents=True)
    if has_data:
        archive.write_bytes(b"local archive")
    plan = client.post("/api/data/pairs/delete/plan", json=payload)
    assert plan.status_code == 200, plan.text
    assert collector in stopped
    assert not next(p for p in manager.subscriptions._read() if p.symbol == "ETHUSDT").realtime_enabled
    request = {**payload, "deletion_snapshot": plan.json()["snapshot"], "confirmation": "DELETE ETHUSDT"}
    assert client.post("/api/data/pairs/delete", json={**request, "confirmation": "DELETE BTCUSDT"}).status_code == 422
    assert client.post("/api/data/pairs/delete", json=request, headers={"origin": "https://evil.example"}).status_code == 403
    if has_data:
        archive.write_bytes(b"changed")
        assert client.post("/api/data/pairs/delete", json=request).status_code == 409
    request["deletion_snapshot"] = client.post("/api/data/pairs/delete/plan", json=payload).json()["snapshot"]
    settings = manager.request(DataJobRequest(**PARAMETERS, action="validate"))[1]

    class Worker:
        returncode = 0 if worker_succeeds else 1
        def wait(self, timeout):
            if worker_succeeds:
                result = delete_pair_data("ETHUSDT", "futures_um", settings, expected_snapshot=request["deletion_snapshot"], project=root)
                (manager.directory / f"{identity}.json").write_text(json.dumps(result))

    def spawn(command, **kwargs):
        nonlocal identity
        identity = command[-1]
        assert "delete_pair" in command
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as denied:
            manager.subscriptions.save(DataPolicy(**payload, realtime_enabled=False))
        assert denied.value.status_code == 409
        return Worker()

    identity = ""
    monkeypatch.setattr("engine.supervisor.subprocess.Popen", spawn)
    response = client.post("/api/data/pairs/delete", json=request)
    assert response.status_code == 202, response.text
    for thread in manager.threads:
        thread.join(timeout=10)
    detail = client.get(f"/api/data/jobs/{identity}").json()
    assert detail["status"] == ("succeeded" if worker_succeeds else "failed"), detail
    assert archive.exists() == (has_data and not worker_succeeds)
    policies = manager.subscriptions._read()
    assert any(p.symbol == "ETHUSDT" for p in policies) is not worker_succeeds
    assert any(p.symbol == "BTCUSDT" for p in policies)
    assert not manager.subscriptions.deleting


def test_pair_delete_empty_configuration_exclusion_and_mount_failure(context):
    client, app, root = context
    from api.market_data.contracts import DataPolicy
    login(client)
    manager = app.state.market_data
    manager.subscriptions.save(DataPolicy(symbol="ETHUSDT", realtime_enabled=False))
    lease = manager._lease("ETHUSDT")
    try:
        assert client.post("/api/data/pairs/delete/plan", json={"symbol": "ETHUSDT"}).status_code == 409
    finally:
        lease.close()
    plan = client.post("/api/data/pairs/delete/plan", json={"symbol": "ETHUSDT"})
    assert plan.status_code == 200 and plan.json()["file_count"] == 0
    assert client.post("/api/data/pairs/delete/plan", json={"symbol": "../private"}).status_code == 422
    shutil.rmtree(root / "data")
    assert client.post("/api/data/pairs/delete/plan", json={"symbol": "ETHUSDT"}).status_code == 503
    assert manager.policy_repository.list()[0]["symbol"] == "ETHUSDT"


def test_pair_delete_snapshot_survives_inventory_polling_and_excludes_readers(context):
    import sqlite3

    from api.market_data.contracts import DataPolicy
    from engine.feed.pair_store import PairStore

    client, app, _root = context
    login(client)
    manager = app.state.market_data
    policy = DataPolicy(symbol="ETHUSDT", base_level="tick", realtime_enabled=False)
    manager.subscriptions.save(policy)
    root = manager.subscriptions.root(policy)
    root.mkdir(parents=True)
    path = root / "collection.sqlite"
    store = PairStore(path, policy.symbol, policy.base_level)
    # Retain WAL sidecars as after an interrupted collector. A read-only
    # connection still updates shared-memory read marks on each query.
    reader = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
    reader.execute("SELECT count(*) FROM trades").fetchone()
    store.close()
    try:
        first = client.post("/api/data/pairs/delete/plan", json={"symbol": policy.symbol})
        assert first.status_code == 200, first.text
        assert client.get("/api/data/datasets?all_levels=true").status_code == 200
        second = client.post("/api/data/pairs/delete/plan", json={"symbol": policy.symbol})
        assert second.status_code == 200, second.text
        assert first.json()["snapshot"] == second.json()["snapshot"]
        with manager._lease(policy.symbol, access=True):
            assert client.get("/api/data/datasets?all_levels=true").status_code == 409
            assert client.get("/api/data/ticks", params={**PARAMETERS, "interval": "tick"}).status_code == 409
    finally:
        reader.close()


def test_different_pair_jobs_run_together_and_recovery_does_not_interrupt_live_workers(context, monkeypatch):
    client, app, root = context
    login(client)
    manager = app.state.market_data
    original = subprocess.Popen
    def worker(command, **kwargs):
        return original([sys.executable, "-c", "import time; time.sleep(30)"], **kwargs)
    monkeypatch.setattr("engine.supervisor.subprocess.Popen", worker)
    first = client.post("/api/data/jobs", json={**PARAMETERS, "action": "validate"})
    second = client.post("/api/data/jobs", json={**PARAMETERS, "symbol": "XRPUSDT", "action": "validate"})
    assert first.status_code == second.status_code == 202
    assert len(manager.processes) == 2
    assert client.post("/api/data/jobs", json={**PARAMETERS, "action": "validate"}).status_code == 409
    other = DataService(app.state.database, project=root)
    try:
        foreign = other.start(DataJobRequest(**{**PARAMETERS, "symbol": "BTCUSDT"}, action="validate"))
        orphan = {"id": "e" * 32, "action": "tick_data:prepare", "status": "running",
                  "created_at": datetime.now(UTC).isoformat(), "finished_at": None,
                  "parameters": {**PARAMETERS, "symbol": "SOLUSDT"}, "result": None, "error": None}
        manager.repository.save_job(orphan)
        # Reconcile an unrelated orphan even while this API owns other live jobs.
        manager.jobs()
        records = {job["id"]: job for job in manager._records()}
        assert records[orphan["id"]]["status"] == "interrupted"
        assert all(records[identity]["status"] == "running" for identity in (first.json()["id"], second.json()["id"], foreign["id"]))
        manager.cancel(first.json()["id"])
        assert manager.processes[second.json()["id"]][0].poll() is None
        assert other.processes[foreign["id"]][0].poll() is None
    finally:
        other.close()


def test_realtime_configuration_coexists_with_history_and_does_not_restart_unchanged_collection(context, monkeypatch):
    client, app, _root = context
    login(client)
    manager = app.state.market_data
    spawned, stopped = [], []
    class Collector:
        def poll(self):
            return None
    def spawn(command, **kwargs):
        assert len(kwargs["pass_fds"]) == 2  # ownership plus shared data access
        spawned.append(command)
        return Collector()
    monkeypatch.setattr("engine.supervisor.subprocess.Popen", spawn)
    monkeypatch.setattr(manager, "_stop", stopped.append)
    payload = {"symbol": "ETHUSDT", "base_level": "1m", "intervals": ["5m"], "realtime_enabled": True}
    with manager._lease("ETHUSDT"), manager._lease("ETHUSDT", access=True, shared=True):
        response = client.post("/api/data/subscriptions", json=payload)
        assert response.status_code == 200, response.text
        collector = manager.subscriptions.processes[("futures_um", "ETHUSDT")]
        # Saving historical-only changes leaves the already running collector intact.
        async def earliest(self, endpoint, **params):
            return [[int(datetime(2020, 1, 1, tzinfo=UTC).timestamp() * 1000)]]
        monkeypatch.setattr("engine.connectors.binance.pair_data.PairREST.get", earliest)
        response = client.post("/api/data/subscriptions", json={**payload, "history_enabled": True})
        assert response.status_code == 200 and "history_error" not in response.json()
        assert manager.subscriptions.processes[("futures_um", "ETHUSDT")] is collector
        assert len(spawned) == 4 and len(stopped) == 1
        assert any(command[2] == "engine.pipeline.pair_data" for command in spawned)
        assert client.post("/api/data/subscriptions", json={**payload, "symbol": "XRPUSDT"}).status_code == 200
        assert client.post("/api/data/subscriptions", json={**payload, "realtime_enabled": False}).status_code == 200
        assert collector in stopped and collector not in manager.subscriptions.processes.values()


def test_delete_access_blocks_only_its_pair_and_realtime_reader_prevents_unlink(context):
    client, app, _root = context
    login(client)
    manager = app.state.market_data
    payload = {"symbol": "ETHUSDT", "realtime_enabled": False}
    with manager._lease("ETHUSDT", access=True):
        assert client.post("/api/data/subscriptions", json=payload).status_code == 409
        assert client.post("/api/data/subscriptions", json={**payload, "symbol": "XRPUSDT"}).status_code == 200
    # A surviving collector's descriptor still protects data even when the stored
    # policy says disabled, e.g. after a lost API owner.
    with manager._lease("ETHUSDT", access=True, shared=True):
        job = {**PARAMETERS, "action": "delete", "deletion_snapshot": "0" * 64, "confirmation": "DELETE ETHUSDT"}
        response = client.post("/api/data/jobs", json=job)
        assert response.status_code == 409 and "采集" in response.text
        assert client.post("/api/data/pairs/delete/plan", json={"symbol": "XRPUSDT"}).status_code == 200


def test_worker_inherits_both_pair_locks_after_parent_closes_descriptors(context):
    from fastapi import HTTPException
    _client, app, root = context
    manager = app.state.market_data
    job_lease = manager._lease("ETHUSDT")
    access_lease = manager._lease("ETHUSDT", access=True, shared=True)
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"],
                             pass_fds=(job_lease.fileno(), access_lease.fileno()), start_new_session=True)
    job_lease.close()
    access_lease.close()
    other = DataService(app.state.database, project=root)
    try:
        with pytest.raises(HTTPException) as busy:
            other._lease("ETHUSDT")
        assert busy.value.status_code == 409
        with pytest.raises(HTTPException):
            other._lease("ETHUSDT", access=True)
        with other._lease("ETHUSDT", access=True, shared=True), other._lease("XRPUSDT"):
            pass
    finally:
        manager._stop(child)
        other.close()
    with manager._lease("ETHUSDT"), manager._lease("ETHUSDT", access=True):
        pass


def test_recovery_rechecks_terminal_state_after_acquiring_pair(context, monkeypatch):
    _client, app, _root = context
    manager = app.state.market_data
    record = {"id": "d" * 32, "action": "tick_data:validate", "status": "running", "parameters": PARAMETERS}
    snapshots = iter([[record], [{**record, "status": "succeeded"}]])
    monkeypatch.setattr(manager, "_records", lambda: next(snapshots))
    def unexpected(_record):
        raise AssertionError("Recovery overwrote a completed job")
    monkeypatch.setattr(manager.repository, "save_job", unexpected)
    manager.recover()


def test_rejected_duplicate_pair_delete_preserves_the_running_delete_marker(context):
    client, app, _root = context
    login(client)
    manager = app.state.market_data
    key = ("futures_um", "ETHUSDT")
    manager.subscriptions.deleting.add(key)
    try:
        with manager._lease("ETHUSDT"):
            response = client.post("/api/data/pairs/delete", json={"symbol": "ETHUSDT",
                "deletion_snapshot": "0" * 64, "confirmation": "DELETE ETHUSDT"})
            assert response.status_code == 409
            assert key in manager.subscriptions.deleting
    finally:
        manager.subscriptions.deleting.discard(key)


def test_stream_storage_failure_after_client_disconnect_closes_without_secondary_error(context, monkeypatch):
    import asyncio
    from types import SimpleNamespace

    from starlette.websockets import WebSocketDisconnect

    from api.auth.contracts import UserResponse
    from api.db.errors import StorageUnavailable
    from api.market_data.router import task_stream
    _client, app, _root = context
    user = UserResponse(id=1, username="admin")
    monkeypatch.setattr("api.market_data.router.require_user", lambda *_args: user)
    def unavailable(*_args):
        raise StorageUnavailable("busy")
    monkeypatch.setattr(app.state.market_data, "task_snapshot", unavailable)
    class Socket:
        def __init__(self):
            self.headers = {"origin": "http://testserver", "host": "testserver"}
            self.url = SimpleNamespace(scheme="ws")
            self.cookies = {}
        async def accept(self):
            pass
        async def close(self, code):
            assert code == 1013
            raise WebSocketDisconnect(code=1006)
    socket = Socket()
    socket.app = app
    asyncio.run(task_stream(socket, user))


def test_status_lists_terminal_tasks_once_and_still_recovers_orphans(context, monkeypatch):
    _client, app, _root = context
    manager = app.state.market_data
    base = {"id": "b" * 32, "action": "tick_data:validate", "status": "succeeded",
            "created_at": datetime.now(UTC).isoformat(), "finished_at": datetime.now(UTC).isoformat(),
            "parameters": PARAMETERS, "result": None, "error": None}
    manager.repository.save_job(base)
    original = manager.repository.list_jobs
    reads = []
    def read():
        reads.append(True)
        return original()
    monkeypatch.setattr(manager.repository, "list_jobs", read)
    assert manager.jobs()[0]["status"] == "succeeded"
    assert len(reads) == 1
    manager.repository.save_job({**base, "status": "running", "finished_at": None})
    reads.clear()
    assert manager.jobs()[0]["status"] == "interrupted"
    assert len(reads) == 2  # Fresh state is checked after acquiring the pair lease.


def test_configuration_only_in_database_operations_and_monitor_only_in_files(context, monkeypatch):
    from sqlalchemy import func, select

    from api.db.errors import StorageUnavailable
    from api.db.models import ManagementJob, MarketDataPolicy
    from api.market_data.contracts import DataPolicy
    from api.market_data.subscriptions import Subscriptions
    client, app, root = context
    login(client)
    manager = app.state.market_data
    manager.subscriptions.save(DataPolicy(symbol="ETHUSDT", realtime_enabled=False))
    with app.state.database.session() as session:
        assert session.scalar(select(func.count()).select_from(MarketDataPolicy)) == 1
        assert session.scalar(select(func.count()).select_from(ManagementJob)) == 0
    manager.repository.save_job({"id": "c" * 32, "action": "tick_data:validate", "status": "succeeded",
                                 "created_at": datetime.now(UTC).isoformat(), "parameters": PARAMETERS,
                                 "finished_at": None, "result": None, "error": None})
    assert len(list((root / ".local/state/api/data_jobs/operations").glob("*.json"))) == 1
    manager.subscriptions.close()
    manager.subscriptions = Subscriptions(manager)
    manager.subscriptions.restore()
    assert manager.subscriptions._read()[0].symbol == "ETHUSDT"
    def offline(*_args, **_kwargs):
        raise StorageUnavailable("offline")
    monkeypatch.setattr(manager.policy_repository, "list", offline)
    manager.subscriptions.restore()
    assert manager.subscriptions._read()[0].symbol == "ETHUSDT"
    manager.monitor.sample()
    value = client.get("/api/data/services").json()
    assert value["configuration_error"] == "offline" and value["state"] == "DEGRADED"
    assert value["items"][0]["services"]["history"]["state"] == "STOPPED"
    saved = json.loads(manager.monitor.path.read_text())
    saved["valid_until_ms"] = 0
    manager.monitor.path.write_text(json.dumps(saved))
    assert client.get("/api/data/services").json()["reason"] == "monitor_expired"
    assert all(not state["healthy"] for state in manager.monitor.read()["items"][0]["services"].values())
    with app.state.database.session() as session:
        assert session.scalar(select(func.count()).select_from(ManagementJob)) == 0


def test_monitor_rejects_expired_service_evidence_and_status_reads_do_not_wait_for_configuration_lock(context):
    import threading

    from api.market_data.contracts import DataPolicy
    _client, app, _root = context
    manager = app.state.market_data
    policy = DataPolicy(symbol="ETHUSDT", base_level="tick", realtime_enabled=False)
    manager.subscriptions.save(policy)
    # Model an enabled service without launching network workers.
    manager.subscriptions.policies = [policy.model_copy(update={"realtime_enabled": True})]
    class Process:
        def poll(self):
            return None
    manager.subscriptions.processes[("futures_um", "ETHUSDT")] = Process()
    manager.subscriptions.auxiliary[("futures_um", "ETHUSDT", "calculation")] = Process()
    root = manager.subscriptions.root(policy)
    root.mkdir(parents=True)
    (root / "status.json").write_text(json.dumps({"state": "READY", "valid_until_ms": 0, "backlog": 7}))
    value = manager.monitor.sample()
    observation = value["items"][0]["services"]["collection"]
    assert not observation["healthy"] and observation["reason"] == "status_expired"
    assert observation["backlog"] == 7
    done = threading.Event()
    with manager.subscriptions.lock:
        reader = threading.Thread(target=lambda: (manager.subscriptions.items(), manager.monitor.sample(), done.set()))
        reader.start()
        try:
            assert done.wait(2), "File status readers waited behind configuration I/O"
        finally:
            # Fake process instances are not OS-owned and must never be signalled.
            manager.subscriptions.processes.clear()
            manager.subscriptions.auxiliary.clear()
    reader.join()


def test_corrupt_legacy_operation_does_not_hide_service_monitor_or_storage(context):
    client, app, _root = context
    login(client)
    manager = app.state.market_data
    directory = manager.repository.directory
    directory.mkdir(parents=True)
    broken = directory / ('d' * 32 + '.json')
    broken.write_text('incomplete write evidence')
    response = client.get('/api/data/status')
    assert response.status_code == 200 and response.json()['storage']['available']
    assert response.json()['operation_error'] and response.json()['tasks'] == []
    assert client.get('/api/data/services').status_code == 200
    assert client.get('/api/data/jobs').status_code == 503
    assert broken.read_text() == 'incomplete write evidence'


def test_explicit_configuration_import_preserves_database_parameters_and_never_starts_services(context, monkeypatch):
    import api.market_data.import_configuration as importer
    from api.market_data.contracts import DataPolicy
    _client, app, root = context
    manager = app.state.market_data
    manager.subscriptions.save(DataPolicy(symbol='ETHUSDT', realtime_enabled=False))
    source = root / 'legacy-parameters.json'
    source.write_text(json.dumps([{'symbol': 'ETHUSDT', 'realtime_enabled': True}, {'symbol': 'XRPUSDT', 'intervals': ['5m']}]))
    content = source.read_text()
    monkeypatch.setattr(importer, 'PROJECT_ROOT', root)
    monkeypatch.setattr(importer, 'create_database', lambda _settings: app.state.database)
    monkeypatch.setattr(sys, 'argv', ['import_configuration', str(source)])
    with pytest.raises(SystemExit):
        importer.main()  # The live API still owns configuration.
    assert len(manager.policy_repository.list()) == 1
    manager.subscriptions.close()
    importer.main()
    policies = {item['symbol']: item for item in manager.policy_repository.list()}
    assert set(policies) == {'ETHUSDT', 'XRPUSDT'}
    assert not policies['ETHUSDT']['realtime_enabled'] and not policies['XRPUSDT']['realtime_enabled']
    assert policies['XRPUSDT']['base_level'] == 'auto'
    assert source.read_text() == content and not manager.subscriptions.processes


def test_history_start_is_utc_midnight_and_duration_is_persisted_once(context, monkeypatch):
    client, app, _root = context
    login(client)
    manager = app.state.market_data
    monkeypatch.setattr(manager.subscriptions, "_start", lambda policy: None)
    payload = {"symbol": "ETHUSDT", "realtime_enabled": False, "history_enabled": True,
               "history_mode": "start", "history_start": "2026-08-02T03:45:12+08:00"}
    response = client.post("/api/data/subscriptions", json=payload)
    assert response.status_code == 200, response.text
    assert datetime.fromisoformat(response.json()["policy"]["history_start"]) == datetime(2026, 8, 1, tzinfo=UTC)
    response = client.post("/api/data/subscriptions", json={**payload, "history_mode": "duration", "history_days": 10})
    saved = response.json()["policy"]
    assert datetime.fromisoformat(saved["history_start"]) == datetime(2026, 7, 22, tzinfo=UTC)
    # A refresh/restart and an unchanged save must not extend the range again.
    manager.subscriptions.policies = None
    manager.subscriptions.restore()
    assert client.get("/api/data/subscriptions").json()["items"][0]["policy"] == saved
    assert client.post("/api/data/subscriptions", json=saved).json()["policy"] == saved
    # Resolving unavailable exchange history fails without changing stored settings.
    async def unavailable(self, endpoint, **params):
        raise OSError("offline")
    monkeypatch.setattr("engine.connectors.binance.pair_data.PairREST.get", unavailable)
    assert client.post("/api/data/subscriptions", json={**saved, "history_mode": "all"}).status_code == 503
    assert client.get("/api/data/subscriptions").json()["items"][0]["policy"] == saved


def test_duration_without_retained_data_uses_today_utc_midnight(context, monkeypatch):
    client, app, root = context
    login(client)
    monkeypatch.setattr(app.state.market_data.subscriptions, "_start", lambda policy: None)
    settings = PipelineSettings("futures_um", "trades", ("ETHUSDT",), ("1m",),
                                root / "data/raw", root / "data/catalog", root / "data/state")
    monkeypatch.setattr("engine.pipeline.config.load_settings", lambda: settings)
    today = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    # Old modes may contain an unused date: it is not a configured fixed start.
    from api.market_data.contracts import DataPolicy
    app.state.market_data.policy_repository.save([DataPolicy(symbol="ETHUSDT", realtime_enabled=False,
        history_mode="all", history_start=datetime(2020, 1, 1, tzinfo=UTC))], tenant_id=1)
    response = client.post("/api/data/subscriptions", json={"symbol": "ETHUSDT", "realtime_enabled": False,
        "history_enabled": True, "history_mode": "duration", "history_days": 10})
    assert response.status_code == 200, response.text
    saved = response.json()["policy"]
    assert datetime.fromisoformat(saved["history_start"]) == today - timedelta(days=10)
    assert saved["history_mode"] == "start"


def test_chart_reads_retained_evidence_and_valid_forming_tail(context, monkeypatch):
    from types import SimpleNamespace

    from api.market_data.contracts import DataPolicy
    from engine.feed.pair_store import PairStore

    client, app, _root = context
    assert client.get('/api/data/chart', params={'symbol': 'ETHUSDT'}).status_code == 401
    login(client)
    now = int(time.time() * 1000)
    left = now // 60000 * 60000
    policy = DataPolicy(symbol='ETHUSDT', base_level='1m', realtime_enabled=True)
    subscriptions = app.state.market_data.subscriptions
    subscriptions.policies = [policy]
    journal_root = subscriptions.root(policy)
    journal_root.mkdir(parents=True, exist_ok=True)
    store = PairStore(journal_root / 'collection.sqlite', 'ETHUSDT', '1m', ['1m'])
    store.candle('1m', [left - 60000, '10', '12', '9', '11', '2', left - 1], now)
    # Working, non-retained evidence must never leak into the chart.
    store.candle('1m', [left - 120000, '10', '12', '9', '11', '2', left - 60001], now, retained=False)
    store.close()
    forming = {'interval': '1m', 'start_ms': left, 'end_ms': left + 60000,
               'open': '11', 'high': '13', 'low': '11', 'close': '12', 'volume': '1', 'is_complete': False, 'is_closed': False, 'updated_ms': now}
    for role in ('collection', 'calculation'):
        subscriptions_role = subscriptions.processes if role == 'collection' else subscriptions.auxiliary
        key = ('futures_um', 'ETHUSDT') if role == 'collection' else ('futures_um', 'ETHUSDT', role)
        subscriptions_role[key] = SimpleNamespace(poll=lambda: None)
        path = journal_root / ('status.json' if role == 'collection' else 'calculation/status.json')
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps({'state': 'READY', 'valid_until_ms': now + 15000,
                                   'bar_updates': [forming, {**forming, 'start_ms': left - 60000, 'end_ms': left,
                                                            'updated_ms': now - 5000}]}))
    try:
        result = client.get('/api/data/chart?symbol=ETHUSDT&interval=1m')
        assert result.status_code == 200, result.text
        data = result.json()
        assert data['live'] and data['quality'] == 'unverified'
        assert [r['time'] for r in data['candles']] == [(left - 60000) // 1000, left // 1000]
        assert not data['candles'][-1]['is_closed'] and not data['candles'][-1]['is_complete']
        # Expired observations cannot keep a forming candle alive.
        monkeypatch.setattr('api.market_data.monitor.time.time', lambda: (now + 30000) / 1000)
        data = client.get('/api/data/chart?symbol=ETHUSDT&interval=1m').json()
        assert not data['live']
        assert len(data['candles']) == 1
        assert client.get('/api/data/chart?symbol=ETHUSDT&interval=2s').status_code == 422
        assert client.get('/api/data/chart?symbol=../ETHUSDT').status_code == 422
        assert client.get('/api/data/chart?symbol=XRPUSDT').json()['candles'] == []
    finally:
        subscriptions.processes.clear()
        subscriptions.auxiliary.clear()


def test_chart_catalog_window_preserves_open_time_precision_and_delete_lock(context):
    import fcntl

    from nautilus_trader.model import Bar, BarType, Price, Quantity

    from core.market.instruments import bar_type_for
    from engine.pipeline.catalog import ParquetDataCatalog

    client, app, root = context
    login(client)
    left = int(time.time() * 1000) // 60000 * 60000
    kind = BarType.from_str(bar_type_for('ETHUSDT', '1m'))
    catalog = ParquetDataCatalog(root / 'data/catalog')
    catalog.write_data([
        Bar(kind, *(Price.from_str(v) for v in ('10.10', '12.20', '9.30', '11.40')),
            Quantity.from_str('2.123'), (opening + 60000 - 1) * 1000000,
            (opening + 60000 - 1) * 1000000)
        for opening in range(left - 301 * 60000, left, 60000)
    ])
    result = client.get('/api/data/chart?symbol=ETHUSDT&interval=1m')
    assert result.status_code == 200, result.text
    data = result.json()
    assert len(data['candles']) == 299
    assert data['candles'][0]['time'] == (left - 299 * 60000) // 1000
    assert data['candles'][-1]['time'] == (left - 60000) // 1000
    assert data['candles'][-1]['close'] == '11.40'
    assert data['candles'][-1]['volume'] == '2.123'
    assert data['candles'][-1]['is_closed']
    assert data['candles'][-1]['is_complete'] is None
    assert not data['live']
    # A view must obey the same deletion exclusion as other reads.
    with app.state.market_data._lease('ETHUSDT', access=True) as lease:
        assert client.get('/api/data/chart?symbol=ETHUSDT').status_code == 409
        fcntl.flock(lease, fcntl.LOCK_UN)
    import shutil
    shutil.rmtree(root / 'data')
    assert client.get('/api/data/chart?symbol=ETHUSDT').status_code == 503
    assert not (root / 'data').exists()


def test_all_options_include_new_contracts_and_preserve_existing_configuration(context, monkeypatch):
    client, app, _root = context
    login(client)
    starts = []
    monkeypatch.setattr(app.state.market_data.subscriptions, "_start", starts.append)
    first, second = "BTC-261225-85000-C", "ETH-270129-3000-P"
    markets = [{"symbol": first}]

    async def catalog(_environment, product="futures_um"):
        assert product == "options"
        return markets

    app.state.exchanges.catalog = catalog
    payload = {"symbol": "ALL", "product": "options", "base_level": "1m",
               "intervals": ["1m", "5m", "15m"], "realtime_enabled": False}
    login(client, username="viewer")
    assert client.post("/api/data/subscriptions", json=payload).status_code == 403
    login(client)
    assert client.post("/api/data/subscriptions", json=payload,
                       headers={"origin": "https://evil.example"}).status_code == 403
    assert client.post("/api/data/subscriptions/plan", json=payload).status_code == 200
    response = client.post("/api/data/subscriptions", json=payload)
    assert response.status_code == 200, response.text
    policies = app.state.market_data.policy_repository.list()
    assert {p["symbol"] for p in policies} == {"ALL", "BTCUSDT", first}
    # Existing per-contract settings survive automatic catalogue discovery.
    response = client.post("/api/data/subscriptions", json={**payload, "symbol": first,
                                                         "base_level": "tick", "intervals": []})
    assert response.status_code == 200, response.text
    markets[:] = [{"symbol": second}, {"symbol": second}]
    subscriptions = app.state.market_data.subscriptions
    import asyncio
    asyncio.run(subscriptions.sync_options(app.state.exchanges))
    subscriptions.policies = None  # Recover from persisted parameters, not browser state.
    subscriptions.restore()
    asyncio.run(subscriptions.sync_options(app.state.exchanges))
    policies = app.state.market_data.policy_repository.list()
    assert {p["symbol"] for p in policies} == {"ALL", "BTCUSDT", "ETHUSDT", first, second}
    assert next(p for p in policies if p["symbol"] == first)["base_level"] == "tick"
    assert not subscriptions.processes and not subscriptions.auxiliary
    assert client.get("/api/data/subscriptions").status_code == 200
    # Discovery outages preserve the scope and all previously included contracts.
    async def unavailable(*args, **kwargs):
        raise OSError("offline")
    app.state.exchanges.catalog = unavailable
    asyncio.run(subscriptions.sync_options(app.state.exchanges))
    assert subscriptions.last_error
    assert app.state.market_data.policy_repository.list() == policies
    # ALL is never a substitute for a concrete contract in data/deletion requests.
    assert client.post("/api/data/prepare/plan", json={**PARAMETERS, "symbol": "ALL", "product": "options"}).status_code == 422
    assert client.post("/api/data/pairs/delete/plan", json={"symbol": "ALL", "product": "options"}).status_code == 422
    app.state.exchanges.catalog = catalog
    enabled = {**payload, "realtime_enabled": True, "history_enabled": True}
    response = client.post("/api/data/subscriptions", json=enabled)
    assert response.status_code == 200, response.text
    assert starts[-1].symbol == "ALL"
    assert all(p["realtime_enabled"] and p["history_enabled"] for p in app.state.market_data.policy_repository.list())
    third = "BTC-270226-90000-P"
    markets.append({"symbol": third})
    asyncio.run(subscriptions.sync_options(app.state.exchanges))
    assert starts[-1].symbol == third and starts[-1].realtime_enabled and starts[-1].history_enabled
    disabled = client.post("/api/data/subscriptions", json=payload)
    assert disabled.status_code == 200
    assert all(not p["realtime_enabled"] and not p["history_enabled"] for p in app.state.market_data.policy_repository.list())
    assert client.post("/api/data/subscriptions", json={**payload, "product": "futures_um"}).status_code == 422


def test_option_supervision_uses_existing_collect_and_history_roles_and_stops_at_expiry(context, monkeypatch):
    from api.market_data.contracts import DataPolicy

    client, app, _root = context
    login(client)
    subscriptions = app.state.market_data.subscriptions
    commands = []
    class Process:
        def poll(self):
            return None
    monkeypatch.setattr("engine.supervisor.subprocess.Popen", lambda cmd, **kw: commands.append(cmd) or Process())
    monkeypatch.setattr(app.state.market_data, "_stop", lambda process: None)
    async def catalog(_env, product="futures_um"):
        return [{"symbol": "BTC-261225-85000-C"}]
    app.state.exchanges.catalog = catalog
    payload = {"symbol": "ALL", "product": "options", "base_level": "tick",
               "intervals": ["1m", "5m", "15m"], "realtime_enabled": True, "history_enabled": True}
    response = client.post("/api/data/subscriptions", json=payload)
    assert response.status_code == 200, response.text
    assert len(commands) == 2
    assert commands[0][2] == "engine.feed.managed" and "--options-config" in commands[0]
    assert commands[1][2] == "engine.pipeline.pair_data" and "--service" in commands[1]
    assert all('"symbol":"ALL"' not in " ".join(cmd) for cmd in commands)
    child = DataPolicy(**{**payload, "symbol": "BTC-261225-85000-C"})
    path = subscriptions.root(child) / "status.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"state": "STOPPED", "reason": "expired", "data_ready": False,
                                "expiry_ms": 1, "valid_until_ms": 1}))
    subscriptions.restore()
    assert len(commands) == 2  # Shared processes remain; the worker owns contract expiry.
    item = next(item for item in client.get("/api/data/subscriptions").json()["items"] if item["policy"]["symbol"] == child.symbol)
    assert item["runtime"]["reason"] == "expired"
    assert not item["runtime"]["data_ready"]
    # Scope edits cannot restart a contract while its deletion is in progress.
    subscriptions.deleting.add(("options", child.symbol))
    assert client.post("/api/data/subscriptions", json=payload).status_code == 409


def test_all_1514_options_use_two_shared_processes_and_dynamic_manifest(context, monkeypatch):
    import asyncio

    client, app, root = context
    login(client)
    markets = [{"symbol": f"BTC-261225-{10000 + i}-C"} for i in range(1514)]
    async def catalog(_env, product="futures_um"):
        return markets
    app.state.exchanges.catalog = catalog
    commands = []
    stopped = []
    class Process:
        def poll(self):
            return None
    def spawn(command, **kwargs):
        commands.append((command, kwargs))
        return Process()
    monkeypatch.setattr("engine.supervisor.subprocess.Popen", spawn)
    monkeypatch.setattr(app.state.market_data, "_stop", stopped.append)
    payload = {"product": "options", "symbol": "ALL", "realtime_enabled": True, "history_enabled": True}
    assert client.post("/api/data/subscriptions/plan", json=payload).status_code == 200
    response = client.post("/api/data/subscriptions", json=payload)
    assert response.status_code == 200, response.text
    assert len(commands) == 2 and all(len(kwargs["pass_fds"]) == 1 for _cmd, kwargs in commands)
    path = root / ".local/state/api/data_jobs/options-runtime.json"
    assert len(json.loads(path.read_text())["policies"]) == 1514
    assert len(app.state.market_data.policy_repository.list()) == 1516
    assert [item["policy"]["symbol"] for item in client.get("/api/data/subscriptions?group_options=true").json()["items"]] == ["BTCUSDT"]
    subscriptions = app.state.market_data.subscriptions
    assert set(subscriptions.processes) == {("options", "ALL")}
    assert set(subscriptions.auxiliary) == {("options", "ALL", "history")}
    # Newly listed contracts update the manifest without starting any new process.
    markets.append({"symbol": "BTC-270129-20000-P"})
    asyncio.run(subscriptions.sync_options(app.state.exchanges))
    assert len(json.loads(path.read_text())["policies"]) == 1515
    assert len(commands) == 2
    response = client.post("/api/data/subscriptions", json={**payload, "symbol": markets[0]["symbol"], "base_level": "tick"})
    assert response.status_code == 200, response.text
    assert len(commands) == 2 and stopped == []
    policies = json.loads(path.read_text())["policies"]
    assert next(p for p in policies if p["symbol"] == markets[0]["symbol"])["base_level"] == "tick"
    assert next(p for p in policies if p["symbol"] == markets[1]["symbol"])["base_level"] == "1m"
    # A damaged shared process is restarted once; the other role stays running.
    class Exited:
        def poll(self):
            return 1
    subscriptions.processes[("options", "ALL")] = Exited()
    subscriptions.restore()
    assert len(commands) == 3 and commands[-1][0][2] == "engine.feed.managed"
    assert client.post("/api/data/subscriptions", json={**payload, "realtime_enabled": False, "history_enabled": False}).status_code == 200
    assert not subscriptions.processes and not subscriptions.auxiliary
    assert len(stopped) == 2


def test_option_pair_delete_waits_for_its_worker_without_stopping_the_batch(context, monkeypatch):
    import threading

    client, app, _root = context
    login(client)
    symbols = ["BTC-261225-85000-C", "BTC-261225-86000-C"]
    async def catalog(_env, product="futures_um"):
        return [{"symbol": symbol} for symbol in symbols]
    app.state.exchanges.catalog = catalog
    commands, stopped = [], []
    class Process:
        def poll(self):
            return None
    monkeypatch.setattr("engine.supervisor.subprocess.Popen", lambda cmd, **kw: commands.append(cmd) or Process())
    monkeypatch.setattr(app.state.market_data, "_stop", stopped.append)
    payload = {"product": "options", "symbol": "ALL", "realtime_enabled": True, "history_enabled": True}
    assert client.post("/api/data/subscriptions", json=payload).status_code == 200
    lease = app.state.market_data._lease(symbols[0], "options", access=True, shared=True)
    timer = threading.Timer(.2, lease.close)
    timer.start()
    try:
        response = client.post("/api/data/pairs/delete/plan", json={"product": "options", "symbol": symbols[0]})
        assert response.status_code == 200, response.text
    finally:
        timer.join()
        lease.close()
    assert len(commands) == 2 and stopped == []
    policies = {p["symbol"]: p for p in app.state.market_data.policy_repository.list()}
    assert not policies[symbols[0]]["realtime_enabled"] and not policies[symbols[0]]["history_enabled"]
    assert policies[symbols[1]]["realtime_enabled"] and policies[symbols[1]]["history_enabled"]


def test_option_underlying_scope_manages_all_expiries_and_discovers_only_its_contracts(context, monkeypatch):
    import asyncio

    client, app, _root = context
    login(client)
    manager = app.state.market_data
    monkeypatch.setattr(manager.subscriptions, "_start", lambda policy: None)
    markets = [{"symbol": s} for s in ("BTC-261225-85000-C", "BTC-270129-90000-P", "ETH-261225-3000-C")]

    async def catalog(_env, product="options"):
        return markets

    app.state.exchanges.catalog = catalog
    payload = {"product": "options", "symbol": "BTCUSDT", "base_level": "1m", "realtime_enabled": False}
    assert client.post("/api/data/subscriptions", json=payload).status_code == 200
    policies = {p["symbol"]: p for p in manager.policy_repository.list()}
    assert set(policies) == {"BTCUSDT", "BTC-261225-85000-C", "BTC-270129-90000-P"}
    assert client.post("/api/data/subscriptions", json={**payload, "base_level": "tick"}).status_code == 200
    markets.append({"symbol": "BTC-270226-95000-C"})
    asyncio.run(manager.subscriptions.sync_options(app.state.exchanges))
    policies = manager.policy_repository.list()
    assert len(policies) == 4 and all(p["base_level"] == "tick" for p in policies)
    grouped = client.get("/api/data/subscriptions?group_options=true").json()["items"]
    assert [item["policy"]["symbol"] for item in grouped] == ["BTCUSDT"]
    assert len(client.get("/api/data/subscriptions").json()["items"]) == 4
    assert client.post("/api/data/subscriptions", json={**payload, "symbol": "UNKNOWNUSDT", "realtime_enabled": True}).status_code == 422
    assert client.post("/api/data/prepare/plan", json={**PARAMETERS, "symbol": "BTCUSDT", "product": "options"}).status_code == 422
    manager.subscriptions.deleting.add(("options", "BTCUSDT"))
    assert client.post("/api/data/subscriptions", json=payload).status_code == 409
    assert client.post("/api/data/subscriptions", json={**payload, "symbol": "BTC-261225-85000-C"}).status_code == 409
    manager.subscriptions.deleting.clear()


def test_option_group_status_keeps_stale_and_failed_evidence_visible(context, monkeypatch):
    from api.market_data.contracts import DataPolicy

    _client, app, _root = context
    subscriptions = app.state.market_data.subscriptions
    symbols = ("BTC-261225-85000-C", "BTC-270129-90000-P")
    policies = [DataPolicy(product="options", symbol=s) for s in ("BTCUSDT", *symbols)]
    subscriptions._write(policies)
    class Process:
        def poll(self):
            return None
    subscriptions.processes[("options", "ALL")] = Process()
    monkeypatch.setattr(app.state.market_data, "_stop", lambda process: None)
    now = int(time.time() * 1000)
    for p in policies[1:]:
        path = subscriptions.root(p) / "status.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"state": "READY", "data_ready": True, "reason": "collecting",
                                    "checked_ms": now, "valid_until_ms": now + 60000}))
    assert subscriptions.items(group_options=True)[0]["runtime"]["data_ready"]
    reader = DataService(app.state.database, project=_root)
    reader.subscriptions.policies = policies
    assert reader.subscriptions.items(group_options=True)[0]["runtime"]["data_ready"]
    path.write_text(json.dumps({"state": "READY", "data_ready": True, "checked_ms": 1, "valid_until_ms": 1}))
    item = subscriptions.items(group_options=True)[0]
    assert not item["runtime"]["data_ready"] and item["runtime"]["state"] == "DEGRADED"
    assert item["services"]["collection"]["state"] == "DEGRADED"
    sample = app.state.market_data.monitor.sample()
    assert [item["symbol"] for item in sample["items"]] == ["BTCUSDT"]


def test_option_group_deletion_pauses_every_contract_but_preserves_other_underlyings(context, monkeypatch):
    import threading

    from api.market_data.contracts import DataPolicy
    from engine.pipeline.pair_deletion import delete_pair_data

    client, app, root = context
    login(client)
    manager = app.state.market_data
    monkeypatch.setattr(manager.subscriptions, "_start_options", lambda: None)
    symbols = ("ALL", "BTCUSDT", "BTC-261225-85000-C", "BTC-270129-90000-P", "ETHUSDT", "ETH-261225-3000-C")
    manager.subscriptions._write([DataPolicy(product="options", symbol=s) for s in symbols])
    source = root / "data/raw/options/BTC-261225-85000-C/source.json"
    source.parent.mkdir(parents=True)
    source.write_text("evidence")
    leases = [manager._lease(symbol, "options", access=True, shared=True)
              for symbol in (symbols[2], symbols[2], symbols[3], symbols[-1])]
    final_write = source.with_name("last-write.json")
    def finish_writes():
        final_write.write_text("last evidence")
        for lease in leases[:-1]:
            lease.close()
    timer = threading.Timer(.2, finish_writes)
    timer.start()
    try:
        response = client.post("/api/data/pairs/delete/plan", json={"symbol": "BTCUSDT", "product": "options"})
        assert response.status_code == 200, response.text
        assert final_write.exists()
        assert response.json()["file_count"] == 2
        assert all(lease.closed for lease in leases[:-1])
        assert not leases[-1].closed  # Other underlyings may continue writing.
    finally:
        timer.join()
        for lease in leases:
            lease.close()
    policies = {p["symbol"]: p for p in manager.policy_repository.list()}
    assert not any(p["realtime_enabled"] for s, p in policies.items() if s.startswith("BTC"))
    assert policies["ETHUSDT"]["realtime_enabled"] and policies["ETH-261225-3000-C"]["realtime_enabled"]
    snapshot = response.json()["snapshot"]
    settings = manager.request(DataJobRequest(**PARAMETERS, action="validate"))[1]
    identity = ""
    class Worker:
        returncode = 0
        def wait(self, timeout):
            result = delete_pair_data("BTCUSDT", "options", settings, expected_snapshot=snapshot, project=root)
            (manager.directory / f"{identity}.json").write_text(json.dumps(result))
    def spawn(command, **kwargs):
        nonlocal identity
        identity = command[-1]
        return Worker()
    monkeypatch.setattr("engine.supervisor.subprocess.Popen", spawn)
    response = client.post("/api/data/pairs/delete", json={"symbol": "BTCUSDT", "product": "options",
                           "deletion_snapshot": snapshot, "confirmation": "DELETE BTCUSDT"})
    assert response.status_code == 202, response.text
    for thread in manager.threads:
        thread.join(timeout=10)
    detail = client.get(f"/api/data/jobs/{identity}").json()
    assert detail["status"] == "succeeded", detail
    assert not source.exists() and not final_write.exists()
    assert {p["symbol"] for p in manager.policy_repository.list()} == {"ETHUSDT", "ETH-261225-3000-C"}
    assert [item["policy"]["symbol"] for item in client.get("/api/data/subscriptions?group_options=true").json()["items"]] == ["ETHUSDT"]


def test_option_group_delete_timeout_retains_paused_configuration_and_data(context, monkeypatch):
    from api.market_data.contracts import DataPolicy

    client, app, root = context
    login(client)
    manager = app.state.market_data
    monkeypatch.setattr(manager.subscriptions, "_start_options", lambda: None)
    symbols = ("BTCUSDT", "BTC-261225-85000-C", "ETHUSDT", "ETH-261225-3000-C")
    manager.subscriptions._write([DataPolicy(product="options", symbol=s, history_enabled=True) for s in symbols])
    source = root / "data/raw/options/BTC-261225-85000-C/source.json"
    source.parent.mkdir(parents=True)
    source.write_text("evidence")
    lease = manager._lease(symbols[1], "options", access=True, shared=True)
    try:
        response = client.post("/api/data/pairs/delete/plan", json={"symbol": "BTCUSDT", "product": "options"})
        assert response.status_code == 409, response.text
        assert "重新预览" in response.json()["detail"]
        assert not manager.repository.list_jobs()
        assert source.read_text() == "evidence"
        policies = {p["symbol"]: p for p in manager.policy_repository.list()}
        assert set(policies) == set(symbols)
        assert all(not policies[s]["realtime_enabled"] and not policies[s]["history_enabled"] for s in symbols[:2])
        assert all(policies[s]["realtime_enabled"] and policies[s]["history_enabled"] for s in symbols[2:])
    finally:
        lease.close()
    response = client.post("/api/data/pairs/delete/plan", json={"symbol": "BTCUSDT", "product": "options"})
    assert response.status_code == 200, response.text
    assert response.json()["file_count"] == 1


def test_option_underlying_uses_catalogue_pair_instead_of_assuming_usdt(context, monkeypatch):
    client, app, _root = context
    login(client)
    monkeypatch.setattr(app.state.market_data.subscriptions, "_start", lambda policy: None)
    symbol = "BTC-261225-85000-C"
    async def catalog(_env, product="options"):
        return [{"symbol": symbol, "underlying": "BTCUSDC"}]
    app.state.exchanges.catalog = catalog
    payload = {"symbol": "BTCUSDC", "product": "options", "realtime_enabled": False}
    assert client.post("/api/data/subscriptions", json=payload).status_code == 200
    policies = {p["symbol"]: p for p in app.state.market_data.policy_repository.list()}
    assert policies[symbol]["underlying"] == "BTCUSDC"
    grouped = client.get("/api/data/subscriptions?group_options=true").json()["items"]
    assert [item["policy"]["symbol"] for item in grouped] == ["BTCUSDC"]
    assert client.post("/api/data/subscriptions", json={**payload, "symbol": symbol, "underlying": "ETHUSDC"}).status_code == 422


def test_latest_data_times_read_only_configured_futures_1s(context, monkeypatch):
    import shutil

    from nautilus_trader.model import Bar, BarType, Price, Quantity

    from api.market_data.contracts import DataPolicy
    from core.market.instruments import bar_type_for
    from engine.pipeline.catalog import ParquetDataCatalog

    client, app, root = context
    assert client.get("/api/data/datasets/latest").status_code == 401
    login(client, username="viewer")
    app.state.market_data.subscriptions._write([
        DataPolicy(symbol="ETHUSDT", realtime_enabled=False),
        DataPolicy(symbol="ETHUSDC", realtime_enabled=False),
        DataPolicy(symbol="BTCUSDT", product="options", realtime_enabled=False),
    ])
    assert client.get("/api/data/datasets/latest").json() == {"ETHUSDT": None, "ETHUSDC": None}
    assert not (root / "data/catalog").exists()
    catalog = ParquetDataCatalog(root / "data/catalog")
    start = 1_793_750_400_000_000_000

    def write(symbol, interval, stamp):
        price = Price.from_str("10.10")
        catalog.write_data([Bar(BarType.from_str(bar_type_for(symbol, interval)),
                               price, price, price, price, Quantity.from_str("2.123"), stamp, stamp)])

    write("ETHUSDT", "1s", start + 999_000_000)
    latest = start + 10_999_000_000
    write("ETHUSDT", "1s", latest)
    write("ETHUSDT", "1m", start + 59_999_000_000)
    write("XRPUSDT", "1s", start + 99_999_000_000)  # Unconfigured data never adds a row.
    write("ETHUSDC", "1m", start + 59_999_000_000)  # No fallback from missing 1s.

    def no_inventory(*args, **kwargs):
        pytest.fail("latest time must not query instruments, coverage or candle rows")

    monkeypatch.setattr(ParquetDataCatalog, "instruments", no_inventory)
    monkeypatch.setattr(ParquetDataCatalog, "get_intervals", no_inventory)
    monkeypatch.setattr(ParquetDataCatalog, "bars", no_inventory)
    monkeypatch.setattr("api.market_data.router.dataset_inventory", no_inventory)
    response = client.get("/api/data/datasets/latest")
    assert response.status_code == 200, response.text
    assert response.json() == {"ETHUSDT": str(latest), "ETHUSDC": None}
    # Fresh requests see newly installed 1s data, not a cached timestamp.
    write("ETHUSDT", "1s", latest + 1_000_000_000)
    assert client.get("/api/data/datasets/latest").json()["ETHUSDT"] == str(latest + 1_000_000_000)
    with app.state.market_data._lease("ETHUSDT", access=True):
        assert client.get("/api/data/datasets/latest").status_code == 409
    assert client.get("/api/data/datasets/latest").status_code == 200
    shutil.rmtree(root / "data")
    assert client.get("/api/data/datasets/latest").status_code == 503
    assert not (root / "data").exists()
