"""Route development tenant workloads to a local Docker host or an outbound Agent.

A placement is never inferred from node availability. No automatic migration,
production failover or replay of timed-out trading commands occurs here.
"""
from __future__ import annotations

import os
import secrets
import stat
from pathlib import Path

import httpx

from core.engine_contracts import EngineConflict

from .tenant_docker import DockerTenantManager


class DistributedTenantManager:
    def __init__(self, local: DockerTenantManager, hub, *, image, root,
                 development_cpus, development_memory):
        self.local = local
        self.hub = hub
        self.image = image
        self.root = Path(root).expanduser().resolve()
        self.development_cpus = development_cpus
        self.development_memory = development_memory
        self.market_tenant_id = None

    def bind_market_tenant(self, tenant_id):
        self.market_tenant_id = str(tenant_id)
        if self.hub.assigned(tenant_id) != "local":
            raise EngineConflict("默认行情/正式租户不能部署在远程开发节点。")
        self.local.bind_market_tenant(tenant_id)

    def _node(self, tenant_id):
        return self.hub.assigned(tenant_id)

    @staticmethod
    def _workload_id(tenant_id):
        value = DockerTenantManager._id(tenant_id)
        return f"engine-{value}"

    def _token(self, tenant_id):
        name = DockerTenantManager._id(tenant_id)
        base = self.root / name
        if base.is_symlink():
            raise EngineConflict("远程 Engine 凭据目录不可信。")
        base.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = base / "remote-engine.token"
        if path.is_symlink():
            raise EngineConflict("远程 Engine 令牌不能是符号链接。")
        if not path.exists():
            try:
                descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                pass
            else:
                with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                    handle.write(secrets.token_urlsafe(48))
        if stat.S_IMODE(path.stat().st_mode) & 0o077:
            raise EngineConflict("远程 Engine 令牌权限不安全。")
        token = path.read_text(encoding="utf-8").strip()
        if len(token) < 32:
            raise EngineConflict("远程 Engine 令牌无效。")
        return token

    def ensure(self, tenant_id, purpose, status="active"):
        node_id = self._node(tenant_id)
        if node_id == "local":
            return self.local.ensure(tenant_id, purpose, status)
        if purpose != "development":
            raise EngineConflict("远程节点第一阶段仅允许开发租户，不允许实盘租户。")
        if status == "archived":
            return self.stop(tenant_id)
        if status != "active":
            raise EngineConflict("租户状态无效。")
        spec = {
            "id": self._workload_id(tenant_id),
            "image": self.image,
            "cpus": self.development_cpus,
            "memory": self.development_memory,
            "port": 8766,
            "env": {
                "ENGINE_TOKEN": self._token(tenant_id),
                "ENGINE_TENANT_ID": str(tenant_id),
                "ENGINE_TENANT_PURPOSE": "development",
                "ENGINE_HOST": "0.0.0.0",
                "ENGINE_PORT": "8766",
                "ENGINE_PROJECT": "/app",
                "ENGINE_STATE": "/app/.local/state/engine",
            },
            "mounts": {
                "state": "/app/.local/state/engine",
                "data": "/app/data",
                "local": "/app/.local/run",
                "logs": "/app/logs",
            },
        }
        return self.hub.call(node_id, "ensure", spec, timeout=145)

    def endpoint(self, tenant_id):
        node_id = self._node(tenant_id)
        if node_id == "local":
            return self.local.endpoint(tenant_id)
        return f"node://{node_id}", self._token(tenant_id)

    def relay(self, tenant_id, method, path, headers, *, timeout=120, **kwargs):
        node_id = self._node(tenant_id)
        if node_id == "local":
            raise EngineConflict("本机租户不使用远程中继。")
        if kwargs.keys() - {"json", "params"}:
            raise EngineConflict("远程 Engine 第一阶段不支持该请求类型。")
        result = self.hub.call(node_id, "request", {
            "id": self._workload_id(tenant_id),
            "method": method, "path": path, "headers": headers,
            "json": kwargs.get("json"), "params": kwargs.get("params"),
            "timeout": timeout,
        }, timeout=timeout + 25)
        return httpx.Response(result["status"], json=result["body"],
                              headers=result["headers"])

    def stop(self, tenant_id, *, remove=False, retire=False):
        node_id = self._node(tenant_id)
        if node_id == "local":
            return self.local.stop(tenant_id, remove=remove, retire=retire)
        return self.hub.call(node_id, "stop", {
            "id": self._workload_id(tenant_id), "remove": remove, "retire": retire,
        }, timeout=150)

    def worker_log(self, tenant_id, path):
        if self._node(tenant_id) == "local":
            return self.local.worker_log(tenant_id, path)
        raise EngineConflict("远程 Agent 尚不支持本地文件路径式日志映射。")
