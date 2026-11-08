"""Local Docker lifecycle for tenant-owned resident engines.

Only the API host invokes Docker. Engine containers never receive the Docker
socket, API database, master credentials, or another tenant's volume.
"""
from __future__ import annotations

import fcntl
import json
import os
import re
import secrets
import subprocess
import time
from pathlib import Path
from threading import RLock

import httpx

from engine.runtime import EngineConflict

_TENANT_ID = re.compile(r"^[1-9][0-9]*$")
_MEMORY = re.compile(r"^[1-9][0-9]*(?:[kmgKMG])?$")
_CPU = re.compile(r"^[1-9][0-9]*(?:\.[0-9]+)?$")
_PORT = "8766/tcp"


class DockerTenantManager:
    """One long-lived Engine container per tenant on the local Docker host."""

    def __init__(self, root: Path, *, image: str, production_cpus: str,
                 production_memory: str, development_cpus: str, development_memory: str, log_root: Path):
        self.root = Path(root).expanduser().resolve()
        self.image = image
        self.log_root = Path(log_root).expanduser().resolve()
        self._endpoints = {}
        self.primary_tenant_id = None
        self.primary_data = None
        self.primary_jobs = None
        self.limits = {
            "production": (str(production_cpus), str(production_memory)),
            "development": (str(development_cpus), str(development_memory)),
        }
        if not image or not all(
            _CPU.fullmatch(cpu) and _MEMORY.fullmatch(memory)
            for cpu, memory in self.limits.values()
        ):
            raise ValueError("Invalid Docker Engine image or resource limits")
        self._lock = RLock()

    def bind_primary_data(self, tenant_id, data: Path, jobs: Path):
        """Keep the managed-data API on the original default tenant's storage."""
        tenant_id = self._id(tenant_id)
        data = Path(data).expanduser().resolve()
        if not data.is_dir():
            raise OSError("默认租户历史数据目录不可用；不会自动创建或替换。")
        self.primary_tenant_id = tenant_id
        self.primary_data = data
        self.primary_jobs = Path(jobs).expanduser().resolve()

    @staticmethod
    def _id(tenant_id):
        value = str(tenant_id)
        if not _TENANT_ID.fullmatch(value):
            raise EngineConflict("租户 ID 无效。")
        return value

    def _docker(self, *args, allow_missing=False):
        try:
            done = subprocess.run(
                ["docker", *args], text=True, capture_output=True, check=False, timeout=120,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise OSError("Docker Engine 服务不可用或操作超时。") from exc
        if done.returncode:
            if allow_missing and "No such" in done.stderr:
                return None
            # Do not relay arbitrary docker stderr (it can contain environment details).
            raise OSError("Docker Engine 操作失败；请检查服务端容器日志。")
        return done.stdout

    def _inspect(self, name):
        raw = self._docker("inspect", "--type", "container", name, allow_missing=True)
        return json.loads(raw)[0] if raw else None

    @staticmethod
    def _name(tenant_id):
        return f"seealpha-engine-{tenant_id}"

    def _paths(self, tenant_id):
        root = self.root / tenant_id
        return root, root / "state", root / "data", root / "local", root / "engine.env"

    @staticmethod
    def _validate_container(container, tenant_id, purpose=None):
        labels = container["Config"].get("Labels") or {}
        if (labels.get("seealpha.managed") != "tenant-engine"
                or labels.get("seealpha.tenant") != tenant_id
                or (purpose is not None and labels.get("seealpha.purpose") != purpose)):
            raise EngineConflict("Docker 容器标识与租户不匹配，拒绝复用。")

    def _credentials(self, env_path, tenant_id, purpose, *, create):
        if not env_path.exists():
            if not create:
                raise EngineConflict("租户 Engine 令牌不存在，拒绝连接已有容器。")
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
            descriptor = os.open(env_path, flags, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as file:
                file.write(f"ENGINE_TOKEN={secrets.token_urlsafe(48)}\n")
                file.write(f"ENGINE_TENANT_ID={tenant_id}\n")
                file.write(f"ENGINE_TENANT_PURPOSE={purpose}\n")
                file.write("ENGINE_HOST=0.0.0.0\n")
                file.write("ENGINE_PORT=8766\n")
                file.write("ENGINE_PROJECT=/app\n")
                file.write("ENGINE_STATE=/app/.local/state/engine\n")
        if env_path.is_symlink() or env_path.stat().st_mode & 0o077:
            raise EngineConflict("租户 Engine 凭据文件权限不安全。")
        values = dict(line.split("=", 1) for line in env_path.read_text().splitlines() if "=" in line)
        if (values.get("ENGINE_TENANT_ID") != tenant_id
                or values.get("ENGINE_TENANT_PURPOSE") != purpose
                or not values.get("ENGINE_TOKEN")):
            raise EngineConflict("租户 Engine 凭据配置与租户不一致。")
        return values["ENGINE_TOKEN"]

    @staticmethod
    def _address(container):
        if container["State"]["Running"] is not True:
            raise OSError("租户 Engine 尚未运行。")
        ports = (container.get("NetworkSettings") or {}).get("Ports") or {}
        published = ports.get(_PORT) or []
        if len(published) != 1 or published[0].get("HostIp") != "127.0.0.1":
            raise EngineConflict("Engine 必须仅映射到宿主机 127.0.0.1。")
        port = int(published[0]["HostPort"])
        if not 1 <= port <= 65535:
            raise EngineConflict("租户 Engine 端口无效。")
        return f"http://127.0.0.1:{port}"

    def ensure(self, tenant_id, purpose, status="active"):
        tenant_id = self._id(tenant_id)
        if purpose not in self.limits or status not in {"active", "archived"}:
            raise EngineConflict("租户类型或状态无效。")
        if status == "archived":
            self.stop(tenant_id)
            return
        with self._lock:
            root, state, data, local, env_path = self._paths(tenant_id)
            root.mkdir(parents=True, exist_ok=True, mode=0o700)
            lock = root / "provision.lock"
            with lock.open("a+") as lease:
                fcntl.flock(lease, fcntl.LOCK_EX)
                name = self._name(tenant_id)
                container = self._inspect(name)
                if container is None:
                    for path in (state, data, local, root / "logs"):
                        path.mkdir(parents=True, exist_ok=True, mode=0o700)
                    token = self._credentials(env_path, tenant_id, purpose, create=True)
                    cpu, memory = self.limits[purpose]
                    data_source = self.primary_data if tenant_id == self.primary_tenant_id else data
                    mounts = []
                    if tenant_id == self.primary_tenant_id:
                        self.primary_jobs.mkdir(parents=True, exist_ok=True, mode=0o700)
                        mounts = [
                            "--mount",
                            f"type=bind,src={self.primary_jobs},dst=/app/.local/state/api/data_jobs",
                        ]
                    self._docker(
                        "run", "--detach", "--name", name,
                        "--label", "seealpha.managed=tenant-engine",
                        "--label", f"seealpha.tenant={tenant_id}",
                        "--label", f"seealpha.purpose={purpose}",
                        "--cpus", cpu, "--memory", memory, "--memory-swap", memory,
                        "--pids-limit", "256", "--cap-drop", "ALL",
                        "--security-opt", "no-new-privileges",
                        "--user", f"{os.getuid()}:{os.getgid()}",
                        "--publish", "127.0.0.1::8766",
                        "--env-file", str(env_path),
                        "--mount", f"type=bind,src={state},dst=/app/.local/state/engine",
                        "--mount", f"type=bind,src={data_source},dst=/app/data",
                        "--mount", f"type=bind,src={local},dst=/app/.local/run",
                        "--mount", f"type=bind,src={root / "logs"},dst=/app/logs",
                        *mounts, self.image,
                    )
                    container = self._inspect(name)
                else:
                    self._validate_container(container, tenant_id, purpose)
                    expected_data = self.primary_data if tenant_id == self.primary_tenant_id else data
                    mounts = {item["Destination"]: Path(item["Source"]).resolve()
                              for item in container.get("Mounts", [])}
                    if mounts.get("/app/data") != expected_data.resolve():
                        raise EngineConflict("租户 Engine 的历史数据挂载不匹配，拒绝复用。")
                    token = self._credentials(env_path, tenant_id, purpose, create=False)
                    if not container["State"]["Running"]:
                        self._docker("start", name)
                        container = self._inspect(name)
                address = self._address(container)
                for _ in range(100):
                    try:
                        response = httpx.get(
                            address + "/health",
                            headers={"Authorization": f"Bearer {token}"},
                            timeout=0.8,
                        )
                        if response.status_code == 200:
                            self._endpoints[tenant_id] = (address, token)
                            return
                    except httpx.HTTPError:
                        pass
                    time.sleep(0.2)
                raise OSError("租户 Engine 启动后未通过健康检查。")

    def endpoint(self, tenant_id):
        tenant_id = self._id(tenant_id)
        with self._lock:
            if tenant_id in self._endpoints:
                return self._endpoints[tenant_id]
            _root, _state, _data, _local, env_path = self._paths(tenant_id)
            container = self._inspect(self._name(tenant_id))
            if container is None:
                raise OSError("租户 Engine 未部署。")
            self._validate_container(container, tenant_id)
            purpose = container["Config"]["Labels"]["seealpha.purpose"]
            token = self._credentials(env_path, tenant_id, purpose, create=False)
            self._endpoints[tenant_id] = (self._address(container), token)
            return self._endpoints[tenant_id]

    def stop(self, tenant_id, *, remove=False, retire=False):
        tenant_id = self._id(tenant_id)
        if retire and not remove:
            raise ValueError("Retiring a tenant requires removing its container")
        with self._lock:
            self._endpoints.pop(tenant_id, None)
            container = self._inspect(self._name(tenant_id))
            if container is not None:
                self._validate_container(container, tenant_id)
                if container["State"]["Running"]:
                    self._docker("stop", "--time", "20", self._name(tenant_id))
                if remove:
                    self._docker("rm", self._name(tenant_id))
            if retire:
                # SQLite can reuse an integer ID after deletion. Preserve old files
                # without letting the next tenant with that ID inherit them.
                original = self.root / tenant_id
                if original.exists() or original.is_symlink():
                    retired = self.root / ".retired"
                    retired.mkdir(parents=True, exist_ok=True, mode=0o700)
                    original.rename(retired / f"{tenant_id}-{secrets.token_hex(16)}")

    def worker_log(self, tenant_id, path):
        """Map an API log path into an isolated tenant mount; preserve the API read path."""
        tenant_id = self._id(tenant_id)
        original = Path(path).absolute()
        try:
            relative = original.relative_to(self.log_root)
        except ValueError as exc:
            raise EngineConflict("日志路径必须位于 API 的 logs 目录。") from exc
        if ".." in relative.parts:
            raise EngineConflict("日志路径无效。")
        target = self.root / tenant_id / "logs" / relative
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        original.parent.mkdir(parents=True, exist_ok=True)
        if original.is_symlink():
            if original.resolve() != target:
                raise EngineConflict("日志符号链接不属于当前租户。")
        elif original.exists():
            raise EngineConflict("已有同名日志，请先迁移再切换 Docker 部署。")
        else:
            original.symlink_to(target)
        return str(Path("/app/logs") / relative)
