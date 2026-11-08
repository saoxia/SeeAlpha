"""Tenant Docker boundaries and EngineClient routing without a Docker daemon."""
from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from api.engine_client import EngineClient
from api.tenant_docker import DockerTenantManager
from engine.app import create_app
from engine.config import EngineConfig
from engine.runtime import EngineConflict


class FakeTenantManager:
    def __init__(self):
        self.ensured = []
        self.stopped = []
        self.logs = []

    def ensure(self, tenant_id, purpose, status="active"):
        self.ensured.append((str(tenant_id), purpose, status))

    def endpoint(self, tenant_id):
        return f"http://127.0.0.1:{8700 + int(tenant_id)}", f"secret-{tenant_id}"

    def stop(self, tenant_id, *, remove=False):
        self.stopped.append((str(tenant_id), remove))

    def worker_log(self, tenant_id, path):
        self.logs.append((str(tenant_id), str(path)))
        return "/app/logs/process.log"


def test_engine_container_rejects_other_tenant_and_purpose(tmp_path):
    config = EngineConfig(
        tmp_path / "state", token="secret", project=tmp_path,
        tenant_id="42", tenant_purpose="development",
    )
    app = create_app(config)
    headers = {"Authorization": "Bearer secret"}
    with TestClient(app) as http:
        denied = http.put(
            "/tenants/43", headers=headers, json={"purpose": "development"},
        )
        assert denied.status_code == 403
        assert http.get("/tenants/43/export", headers=headers).status_code == 403
        assert http.put(
            "/tenants/42", headers=headers, json={"purpose": "production"},
        ).status_code == 403
        assert http.put(
            "/tenants/42", headers=headers, json={"purpose": "development"},
        ).status_code == 200
        assert http.post(
            "/tenants/42/runnable", headers=headers, json={"action": "backtest"},
        ).status_code == 200
        assert http.post(
            "/tenants/42/runnable", headers=headers, json={"action": "live"},
        ).status_code == 409
        assert http.get("/health").status_code == 401
        assert http.get("/health", headers=headers).status_code == 200


def test_engine_client_selects_tenant_endpoint_without_cross_tenant_tokens(tmp_path, monkeypatch):
    manager = FakeTenantManager()
    client = EngineClient(tmp_path, url="http://legacy:8766", tenant_manager=manager)
    calls = []

    class HTTP:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def request(self, method, url, **kwargs):
            calls.append((method, url, kwargs["headers"]))
            if "/market/workers" in url:
                return httpx.Response(200, json={"id": "worker-x", "pid": 1})
            return httpx.Response(200, json={"ok": True, "returncode": 0})

    monkeypatch.setattr(httpx, "Client", HTTP)
    client.ensure_tenant(7, "development")
    client.ensure_tenant(8, "production")
    client.runnable(7, "backtest")
    client.runnable(8, "live")
    assert calls[0][1].startswith("http://127.0.0.1:8707/")
    assert calls[1][1].startswith("http://127.0.0.1:8708/")
    assert all(
        headers["Authorization"] == ("Bearer secret-7" if ":8707/" in url else "Bearer secret-8")
        for _, url, headers in calls
    )
    assert manager.ensured == [("7", "development", "active"), ("8", "production", "active")]
    with pytest.raises(EngineConflict):
        client._target("/tenants/8/accounts/1/orders", tenant_id="7")
    with pytest.raises(EngineConflict):
        client.worker("unknown")


def test_docker_worker_paths_are_translated_and_stopped(tmp_path, monkeypatch):
    manager = FakeTenantManager()
    client = EngineClient(tmp_path, tenant_manager=manager)
    calls = []

    class HTTP:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def request(self, method, url, **kwargs):
            calls.append((method, url, kwargs))
            if "/market/workers" in url:
                return httpx.Response(200, json={"id": "w1", "pid": 110})
            return httpx.Response(200, json={"status": "ok", "returncode": 0})

    monkeypatch.setattr(httpx, "Client", HTTP)
    worker = client.spawn_worker(
        ["/host/venv/bin/python", "-m", "engine.pipeline.pair_data"],
        tmp_path / "task.log", tenant_id=7,
    )
    assert calls[0][2]["json"]["command"][0] == "python"
    assert calls[0][2]["json"]["log_path"] == "/app/logs/process.log"
    assert worker.poll() == 0
    assert calls[1][1] == "http://127.0.0.1:8707/workers/w1"
    client.drop_tenant(7)
    assert manager.stopped == [("7", True)]


def test_docker_log_paths_must_be_isolated_and_not_overwritten(tmp_path):
    log_root = tmp_path / "public" / "logs"
    manager = DockerTenantManager(
        tmp_path / "tenants", image="seealpha-engine:test",
        production_cpus="2", production_memory="4g",
        development_cpus="4", development_memory="8g", log_root=log_root,
    )
    source = log_root / "api" / "data" / "example.log"
    mapped = manager.worker_log("7", source)
    assert mapped == "/app/logs/api/data/example.log"
    assert source.is_symlink()
    assert source.resolve() == tmp_path / "tenants" / "7" / "logs" / "api" / "data" / "example.log"
    assert manager.worker_log("7", source) == mapped
    with pytest.raises(EngineConflict):
        manager.worker_log("8", source)
    with pytest.raises(EngineConflict):
        manager.worker_log("7", tmp_path / "elsewhere.log")
    with pytest.raises(EngineConflict):
        manager._id("../tenant")
    with pytest.raises(ValueError):
        DockerTenantManager(
            tmp_path, image="seealpha:test",
            production_cpus="0", production_memory="4g",
            development_cpus="4", development_memory="8g", log_root=log_root,
        )
