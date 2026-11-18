"""Outbound node protocol, generic Docker guardrails and Engine HTTP relay."""
from __future__ import annotations

import json
import stat
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest

from api.distributed_docker import DistributedTenantManager
from api.engine_client import EngineClient
from core.engine_contracts import ENGINE_CONTRACT_HEADER, ENGINE_CONTRACT_VERSION, EngineConflict
from node.agent import NodeAgent
from node.docker import DockerRuntime
from node.hub import NodeHub


def node_config(tmp_path):
    path = tmp_path / "nodes.json"
    path.write_text(json.dumps({
        "nodes": {"edge": "a" * 48, "other": "b" * 48},
        "tenant_nodes": {"7": "edge"},
    }), encoding="utf-8")
    path.chmod(0o600)
    return path


def test_hub_authentication_outbound_round_trip_and_cross_node_isolation(tmp_path):
    path = node_config(tmp_path)
    hub = NodeHub(path)
    with pytest.raises(Exception, match="Node authentication"):
        hub.poll("edge", "Bearer invalid")
    hub.authenticate("edge", "Bearer " + "a" * 48)
    with ThreadPoolExecutor(max_workers=1) as tasks:
        future = tasks.submit(hub.call, "edge", "status", {"id": "worker"}, timeout=3)
        job = hub.poll("edge", "Bearer " + "a" * 48)["command"]
        assert job["action"] == "status"
        assert hub.complete("other", "Bearer " + "b" * 48, {
            "id": job["id"], "ok": True, "result": {},
        }) == {"accepted": False}
        assert hub.complete("edge", "Bearer " + "a" * 48, {
            "id": job["id"], "ok": True, "result": {"running": True},
        }) == {"accepted": True}
        assert future.result(timeout=3) == {"running": True}
    assert hub.assigned("7") == "edge"
    assert hub.assigned("8") == "local"
    assert hub.describe()["nodes"][0]["online"] is True
    with pytest.raises(OSError, match="offline"):
        hub.call("other", "status", {}, timeout=0.1)


def test_hub_rejects_insecure_file_and_unknown_placement(tmp_path):
    path = node_config(tmp_path)
    path.chmod(0o644)
    with pytest.raises(ValueError, match="private"):
        NodeHub(path)
    path.chmod(0o600)
    data = json.loads(path.read_text())
    data["tenant_nodes"]["7"] = "unknown"
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="placement"):
        NodeHub(path)


def test_generic_docker_runtime_enforces_allowlist_limits_and_mounts(tmp_path, monkeypatch):
    runtime = DockerRuntime(
        tmp_path / "workloads", allowed_images={"engine:test", "worker:build"},
        max_containers=2, max_cpus=4, max_memory="8g",
    )
    commands = []
    monkeypatch.setattr(runtime, "_inspect", lambda name: None)

    def docker(*args):
        commands.append(args)
        return "" if args[0] == "ps" else "container"
    monkeypatch.setattr(runtime, "_docker", docker)
    spec = {
        "id": "research-7", "image": "worker:build", "cpus": 2, "memory": "2g",
        "port": 8766, "env": {"WORKER_MODE": "backtest"},
        "mounts": {"data": "/app/data"},
    }
    assert runtime.ensure(spec) == {"id": "research-7", "running": True}
    launch = next(cmd for cmd in commands if cmd[0] == "run")
    assert "--cap-drop" in launch and "--pids-limit" in launch
    assert "127.0.0.1::8766" in launch
    assert "--privileged" not in launch
    env = tmp_path / "workloads" / "research-7" / "container.env"
    assert stat.S_IMODE(env.stat().st_mode) == 0o600
    assert "WORKER_MODE=backtest" in env.read_text()
    assert (tmp_path / "workloads" / "research-7" / "volumes" / "data").exists()

    before = len(commands)
    with pytest.raises(ValueError, match="Image"):
        runtime.ensure({**spec, "image": "unexpected:latest"})
    with pytest.raises(ValueError, match="CPU"):
        runtime.ensure({**spec, "cpus": 16})
    with pytest.raises(ValueError, match="Mount"):
        runtime.ensure({**spec, "mounts": {"data": "/etc/secrets"}})
    with pytest.raises(ValueError, match="environment"):
        runtime.ensure({**spec, "env": {"SECRET": "first\nsecond"}})
    assert len(commands) == before


def test_generic_runtime_preserves_data_when_retiring(tmp_path, monkeypatch):
    runtime = DockerRuntime(tmp_path / "root", allowed_images={"worker:build"})
    monkeypatch.setattr(runtime, "_inspect", lambda name: None)
    source = tmp_path / "root" / "research-7"
    source.mkdir(parents=True)
    (source / "marker").write_text("keep")
    runtime.stop("research-7", remove=True, retire=True)
    assert not source.exists()
    saved = list((tmp_path / "root" / ".retired").glob("research-7-*"))
    assert len(saved) == 1
    assert (saved[0] / "marker").read_text() == "keep"


def test_agent_uses_only_outbound_http_and_returns_result(tmp_path):
    seen = []
    def dispatch(request):
        seen.append(request)
        assert request.headers["Authorization"] == "Bearer " + "a" * 48
        if request.url.path.endswith("/poll"):
            return httpx.Response(200, json={"command": {
                "id": "e" * 32, "action": "status", "payload": {"id": "sample"},
            }})
        assert request.url.path.endswith("/result")
        body = json.loads(request.content)
        assert body == {"id": "e" * 32, "ok": True, "result": {"running": True}}
        return httpx.Response(200, json={"accepted": True})

    class Workloads:
        def execute(self, action, payload):
            assert (action, payload) == ("status", {"id": "sample"})
            return {"running": True}

    agent = NodeAgent(
        server="http://127.0.0.1:8000", node_id="edge", token="a" * 48,
        runtime=Workloads(), transport=httpx.MockTransport(dispatch),
    )
    command = agent.poll_once()
    agent._execute(command)
    assert len(seen) == 2
    with pytest.raises(ValueError, match="HTTPS"):
        NodeAgent(server="http://edge.example", node_id="edge",
                  token="a" * 48, runtime=Workloads())


def test_distributed_tenant_docker_stays_development_only(tmp_path):
    class Local:
        def __init__(self):
            self.actions = []
            self.market_tenant_id = None
        def bind_market_tenant(self, tenant_id):
            self.market_tenant_id = str(tenant_id)
        def ensure(self, tenant_id, purpose, status="active"):
            self.actions.append((str(tenant_id), purpose, status))
        def endpoint(self, tenant_id):
            return "http://127.0.0.1:8766", "local"
        def stop(self, tenant_id, **kwargs):
            self.actions.append(("stop", tenant_id, kwargs))
        def worker_log(self, tenant_id, path):
            return "/app/logs/ok"

    class Hub:
        def __init__(self):
            self.calls = []
        def assigned(self, tenant_id):
            return "edge" if str(tenant_id) == "7" else "local"
        def call(self, node_id, action, payload, **kwargs):
            self.calls.append((node_id, action, payload))
            if action == "request":
                return {"status": 200, "body": {"status": "active"},
                        "headers": {ENGINE_CONTRACT_HEADER: ENGINE_CONTRACT_VERSION}}
            return {"running": True}

    local, hub = Local(), Hub()
    manager = DistributedTenantManager(local, hub, image="engine:test",
                                       root=tmp_path / "tenants",
                                       development_cpus="2", development_memory="4g")
    manager.bind_market_tenant(1)
    manager.ensure(1, "production")
    assert local.actions == [("1", "production", "active")]
    with pytest.raises(EngineConflict, match="开发租户"):
        manager.ensure(7, "production")
    manager.ensure(7, "development")
    assert hub.calls[0][1] == "ensure"
    spec = hub.calls[0][2]
    assert spec["id"] == "engine-7" and spec["env"]["ENGINE_TENANT_PURPOSE"] == "development"
    assert stat.S_IMODE((tmp_path / "tenants" / "7" / "remote-engine.token").stat().st_mode) == 0o600
    with pytest.raises(EngineConflict):
        manager.bind_market_tenant(7)

    client = EngineClient(tmp_path, tenant_manager=manager)
    assert client.ensure_tenant(7, "development")["status"] == "active"
    assert hub.calls[-1][1] == "request"
    assert hub.calls[-1][2]["path"] == "/tenants/7"
    assert hub.calls[-1][2]["headers"][ENGINE_CONTRACT_HEADER] == ENGINE_CONTRACT_VERSION
    manager.stop(7, remove=True, retire=True)
    assert hub.calls[-1][1] == "stop"
    assert hub.calls[-1][2]["retire"] is True
