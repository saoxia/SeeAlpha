"""Explicit, durable administrator placement for user production Engine containers.

A business user/production tenant is created without allocating a host. The
system market-data tenant and development tenants retain their existing lifecycle.
No placement changes are allowed after the first selection: moving a live Engine
requires a separate, controlled migration procedure.
"""
from __future__ import annotations

import fcntl
import json
import os
import re
import stat
from pathlib import Path
from threading import RLock
from uuid import uuid4

from core.engine_contracts import EngineConflict

_TENANT = re.compile(r"^[1-9][0-9]*$")


class ProductionDeploymentManager:
    def __init__(self, manager, root, hub=None):
        self.manager = manager
        self.hub = hub
        self.root = Path(root).expanduser()
        if self.root.is_symlink():
            raise ValueError("Engine tenant root must not be a symlink")
        self.path = self.root / ".production-placements.json"
        self.lock = RLock()
        self.market_tenant_id = None
        placements = self._read()
        self._sync_nodes(placements)

    def _read(self):
        if self.path.is_symlink():
            raise ValueError("Production placement file must not be a symlink")
        if not self.path.exists():
            return {}
        if stat.S_IMODE(self.path.stat().st_mode) & 0o077:
            raise ValueError("Production placement file must be private (chmod 600)")
        placements = json.loads(self.path.read_text(encoding="utf-8"))
        if (not isinstance(placements, dict)
                or any(not _TENANT.fullmatch(key) or not isinstance(node, str)
                       or not node or (node != "local" and (
                           self.hub is None or node not in self.hub.tokens))
                       for key, node in placements.items())):
            raise ValueError("Invalid production Engine placements")
        return placements

    def _sync_nodes(self, placements):
        if self.hub is not None:
            with self.hub.lock:
                self.hub.tenant_nodes.update(placements)

    def choices(self):
        nodes = [{"id": "local", "online": True}]
        if self.hub is not None:
            nodes += self.hub.describe()["nodes"]
        return nodes

    def placement(self, tenant_id):
        return self._read().get(str(tenant_id))

    def eligible(self, tenant_id, purpose):
        return (purpose != "production" or str(tenant_id) == self.market_tenant_id
                or self.placement(tenant_id) is not None)

    def assign(self, tenant_id, node_id):
        identity = str(tenant_id)
        if not _TENANT.fullmatch(identity):
            raise ValueError("无效的租户 ID。")
        if node_id not in {node["id"] for node in self.choices()}:
            raise ValueError("所选节点未注册。")
        if node_id != "local" and not any(
            node["id"] == node_id and node["online"] for node in self.choices()
        ):
            raise ValueError("远程节点未连接，不能部署。")

        with self.lock:
            self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
            lockfile = self.root / ".production-placements.lock"
            if lockfile.is_symlink():
                raise ValueError("Production placement lock must not be a symlink")
            descriptor = os.open(lockfile, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            with os.fdopen(descriptor, "r+") as lease:
                fcntl.flock(lease, fcntl.LOCK_EX)
                current = self._read()
                previous = current.get(identity)
                if previous is not None and previous != node_id:
                    raise ValueError("正式 Engine 已绑定其他节点；禁止未经迁移直接改绑。")
                if previous is None:
                    current[identity] = node_id
                    temporary = self.root / f".production-placements-{uuid4().hex}.tmp"
                    try:
                        fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                        with os.fdopen(fd, "w", encoding="utf-8") as stream:
                            json.dump(current, stream, ensure_ascii=False, sort_keys=True)
                            stream.flush()
                            os.fsync(stream.fileno())
                        os.replace(temporary, self.path)
                    finally:
                        temporary.unlink(missing_ok=True)
                self._sync_nodes(current)
        return node_id

    def bind_market_tenant(self, tenant_id):
        self.market_tenant_id = str(tenant_id)
        return self.manager.bind_market_tenant(tenant_id)

    def ensure(self, tenant_id, purpose, status="active"):
        if not self.eligible(tenant_id, purpose):
            raise EngineConflict("正式租户尚未部署：请管理员先选择服务器并创建 Docker。")
        return self.manager.ensure(tenant_id, purpose, status)

    def endpoint(self, tenant_id):
        return self.manager.endpoint(tenant_id)

    def stop(self, tenant_id, *, remove=False, retire=False):
        return self.manager.stop(tenant_id, remove=remove, retire=retire)

    def worker_log(self, tenant_id, path):
        return self.manager.worker_log(tenant_id, path)

    def relay(self, tenant_id, method, path, headers, **kwargs):
        return self.manager.relay(tenant_id, method, path, headers, **kwargs)
