"""Platform-owned Engine HTTP routing and tenant Docker guards.

These tests run inside platform/.venv, with no Engine source, NautilusTrader,
Docker daemon or exchange credentials. No persistent business state is changed.
"""
from __future__ import annotations

import httpx
import pytest

from api.engine_client import EngineClient
from api.tenant_docker import DockerTenantManager
from core.engine_contracts import (
    ENGINE_CONTRACT_HEADER,
    ENGINE_CONTRACT_VERSION,
    EngineConflict,
    QueryError,
)


class TenantEndpoints:
    def __init__(self):
        self.ensured = []
        self.stopped = []

    def ensure(self, tenant_id, purpose, status="active"):
        self.ensured.append((str(tenant_id), purpose, status))

    def endpoint(self, tenant_id):
        return f"http://127.0.0.1:{8700 + int(tenant_id)}", f"token-{tenant_id}"

    def stop(self, tenant_id, *, remove=False, retire=False):
        self.stopped.append((str(tenant_id), remove, retire))

    def worker_log(self, tenant_id, path):
        return "/app/logs/process.log"


def test_platform_client_sends_http_v2_without_engine_runtime(tmp_path):
    requests = []

    def responder(request):
        requests.append((request.method, request.url.path, request.headers))
        return httpx.Response(
            200,
            json={"tenant_id": "7", "status": "active"},
            headers={ENGINE_CONTRACT_HEADER: ENGINE_CONTRACT_VERSION},
        )

    with pytest.raises(ValueError, match="HTTP"):
        EngineClient(tmp_path, url="inprocess")

    client = EngineClient(
        tmp_path, url="http://engine.example",
        token="isolated-token",
        sync_transport=httpx.MockTransport(responder),
    )
    assert client.ensure_tenant(7, "development")["status"] == "active"
    assert [(method, path) for method, path, _ in requests] == [
        ("PUT", "/tenants/7")
    ]
    assert requests[0][2]["Authorization"] == "Bearer isolated-token"
    assert requests[0][2][ENGINE_CONTRACT_HEADER] == ENGINE_CONTRACT_VERSION
    assert not hasattr(client, "runtime")


@pytest.mark.parametrize("status,headers", [
    (200, {}),
    (426, {ENGINE_CONTRACT_HEADER: "3"}),
])
def test_platform_fails_closed_on_incompatible_engine(tmp_path, status, headers):
    def responder(_request):
        return httpx.Response(status, json={"detail": "not compatible"}, headers=headers)

    client = EngineClient(
        tmp_path, url="http://engine.example",
        sync_transport=httpx.MockTransport(responder),
    )
    with pytest.raises(QueryError, match="契约版本"):
        client.ensure_tenant(7, "development")


def test_engine_offline_fails_without_fallback(tmp_path):
    def unavailable(request):
        raise httpx.ConnectError("isolated Engine unavailable", request=request)

    client = EngineClient(
        tmp_path, url="http://engine.example",
        sync_transport=httpx.MockTransport(unavailable),
    )
    with pytest.raises(OSError, match="交易引擎不可用"):
        client._sync("GET", "/health")


def test_tenant_routing_never_mixes_tokens_or_ids(tmp_path, monkeypatch):
    manager = TenantEndpoints()
    client = EngineClient(tmp_path, tenant_manager=manager)
    calls = []

    class HTTP:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def request(self, method, url, **kwargs):
            calls.append((method, url, kwargs["headers"]))
            return httpx.Response(
                200, json={"status": "active"},
                headers={ENGINE_CONTRACT_HEADER: ENGINE_CONTRACT_VERSION},
            )

    monkeypatch.setattr(httpx, "Client", HTTP)
    client.ensure_tenant(7, "development")
    client.ensure_tenant(8, "production")
    assert manager.ensured == [
        ("7", "development", "active"),
        ("8", "production", "active"),
    ]
    assert calls[0][1].startswith("http://127.0.0.1:8707/")
    assert calls[1][1].startswith("http://127.0.0.1:8708/")
    assert calls[0][2]["Authorization"] == "Bearer token-7"
    assert calls[1][2]["Authorization"] == "Bearer token-8"
    with pytest.raises(EngineConflict, match="租户"):
        client._target("/tenants/8/accounts/1/orders", tenant_id="7")


def test_existing_docker_tenant_refuses_implicit_image_switch(tmp_path, monkeypatch):
    manager = DockerTenantManager(
        tmp_path / "tenants", image="seealpha-engine:new-sha",
        production_cpus="2", production_memory="4g",
        development_cpus="4", development_memory="8g",
        log_root=tmp_path / "logs",
    )
    existing = {
        "Config": {
            "Image": "seealpha-engine:old-sha",
            "Labels": {
                "seealpha.managed": "tenant-engine",
                "seealpha.tenant": "7",
                "seealpha.purpose": "development",
            },
        },
        "State": {"Running": True},
    }
    monkeypatch.setattr(manager, "_inspect", lambda _name: existing)

    def forbidden_docker(*_args):
        raise AssertionError("Must not alter an existing Engine container")

    monkeypatch.setattr(manager, "_docker", forbidden_docker)
    with pytest.raises(EngineConflict, match="镜像"):
        manager.ensure("7", "development")
    with pytest.raises(EngineConflict, match="镜像"):
        manager.endpoint("7")
    assert not (tmp_path / "tenants" / "7" / "engine.env").exists()
