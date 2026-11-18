"""Generic, constrained Docker operations for a remote SeeAlpha node.

The Agent does not know what an Engine, backtest or data worker means.
Platform supplies a workload specification, while the Agent enforces the
host boundary: allowlisted images, private mounts, limits and loopback ports.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import subprocess
from decimal import Decimal, InvalidOperation
from pathlib import Path, PurePosixPath
from threading import RLock

import httpx

_NAME = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_ENV = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
_MEMORY = re.compile(r"^([1-9][0-9]*)([mMgG])$")
_CONTRACT_HEADER = "X-SeeAlpha-Engine-Contract"


def memory_bytes(value):
    match = _MEMORY.fullmatch(str(value))
    if match is None:
        raise ValueError("Invalid memory limit")
    return int(match[1]) * (1024 ** (2 if match[2].lower() == "m" else 3))


class DockerRuntime:
    def __init__(self, root, *, allowed_images, max_containers=8,
                 max_cpus=8, max_memory="16g"):
        self.root = Path(root).expanduser().resolve()
        self.images = frozenset(allowed_images)
        self.max_containers = int(max_containers)
        self.max_cpus = Decimal(str(max_cpus))
        self.max_memory = memory_bytes(max_memory)
        if not self.images or self.max_containers < 1 or self.max_cpus <= 0:
            raise ValueError("Node requires image allowlist and positive resource limits")
        self.lock = RLock()

    @staticmethod
    def _id(value):
        if not isinstance(value, str) or not _NAME.fullmatch(value):
            raise ValueError("Invalid workload ID")
        return value

    @staticmethod
    def _name(workload_id):
        return "seealpha-node-" + DockerRuntime._id(workload_id)

    @staticmethod
    def _docker(*args):
        try:
            done = subprocess.run(["docker", *args], capture_output=True, text=True,
                                  check=False, timeout=120)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise OSError("Docker daemon unavailable or timed out") from exc
        if done.returncode:
            # CLI diagnostics may contain private environment data.
            raise OSError("Docker command failed")
        return done.stdout

    def _inspect(self, name):
        try:
            raw = self._docker("inspect", "--type", "container", name)
        except OSError:
            # Only Docker's explicit not-found status is allowed here, not any failure.
            try:
                known = self._docker("ps", "-a", "--format", "{{.Names}}")
            except OSError:
                raise
            if name not in known.splitlines():
                return None
            raise
        return json.loads(raw)[0]

    def _spec(self, spec):
        if not isinstance(spec, dict):
            raise ValueError("Invalid workload definition")
        name = self._id(spec.get("id"))
        image = spec.get("image")
        if image not in self.images:
            raise ValueError("Image not permitted on this node")
        try:
            cpus = Decimal(str(spec["cpus"]))
        except (KeyError, InvalidOperation):
            raise ValueError("Invalid CPU limit") from None
        memory = str(spec.get("memory", ""))
        if not cpus.is_finite() or cpus <= 0 or cpus > self.max_cpus:
            raise ValueError("CPU limit exceeds node policy")
        if memory_bytes(memory) > self.max_memory:
            raise ValueError("Memory limit exceeds node policy")
        env = spec.get("env", {})
        mounts = spec.get("mounts", {})
        port = spec.get("port")
        if (not isinstance(env, dict) or len(env) > 40
                or any(not isinstance(k, str) or not _ENV.fullmatch(k)
                       or not isinstance(v, str) or len(v) > 8192
                       or "\\n" in v or "\\r" in v for k, v in env.items())):
            raise ValueError("Invalid environment")
        if not isinstance(mounts, dict) or len(mounts) > 12:
            raise ValueError("Invalid mounts")
        for volume, target in mounts.items():
            path = PurePosixPath(target) if isinstance(target, str) else None
            if (not isinstance(volume, str) or not _NAME.fullmatch(volume)
                    or path is None or not path.is_absolute()
                    or ".." in path.parts or not str(path).startswith("/app/")):
                raise ValueError("Mount is outside the permitted container tree")
        if port is not None and (type(port) is not int or not 1 <= port <= 65535):
            raise ValueError("Invalid container port")
        return name, image, str(cpus), memory, env, mounts, port

    def _checked(self, container, workload_id):
        labels = (container.get("Config") or {}).get("Labels") or {}
        if (labels.get("seealpha.node-managed") != "true"
                or labels.get("seealpha.workload") != workload_id):
            raise ValueError("Container is not owned by this workload")
        return labels

    def ensure(self, spec):
        workload_id, image, cpus, memory, env, mounts, port = self._spec(spec)
        fingerprint = hashlib.sha256(
            json.dumps(spec, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        name = self._name(workload_id)
        with self.lock:
            container = self._inspect(name)
            if container is not None:
                labels = self._checked(container, workload_id)
                if labels.get("seealpha.spec") != fingerprint:
                    raise ValueError("Workload specification changed; manual migration required")
                if not container["State"]["Running"]:
                    self._docker("start", name)
                return {"id": workload_id, "running": True}
            managed = self._docker(
                "ps", "-a", "--filter", "label=seealpha.node-managed=true",
                "--format", "{{.Names}}",
            ).splitlines()
            if len(managed) >= self.max_containers:
                raise ValueError("Node workload capacity exhausted")
            base = self.root / workload_id
            if base.is_symlink():
                raise ValueError("Workload directory is not trusted")
            base.mkdir(parents=True, exist_ok=True, mode=0o700)
            base.chmod(0o700)
            args = [
                "run", "-d", "--name", name,
                "--label", "seealpha.node-managed=true",
                "--label", f"seealpha.workload={workload_id}",
                "--label", f"seealpha.spec={fingerprint}",
                "--cpus", cpus, "--memory", memory, "--memory-swap", memory,
                "--pids-limit", "256", "--cap-drop", "ALL",
                "--security-opt", "no-new-privileges",
                "--restart", "unless-stopped",
            ]
            if hasattr(os, "getuid") and hasattr(os, "getgid"):
                args.extend(["--user", f"{os.getuid()}:{os.getgid()}"])
            if port is not None:
                args.extend(["--publish", f"127.0.0.1::{port}"])
            volume_root = base / "volumes"
            if volume_root.is_symlink():
                raise ValueError("Workload volume root is not trusted")
            for volume, target in sorted(mounts.items()):
                source = volume_root / volume
                if source.is_symlink():
                    raise ValueError("Workload volume is not trusted")
                source.mkdir(parents=True, exist_ok=True, mode=0o700)
                args.extend(["--mount", f"type=bind,src={source},dst={target}"])
            env_path = base / "container.env"
            if env_path.is_symlink():
                raise ValueError("Workload environment file is not trusted")
            temporary = base / (".env-" + secrets.token_hex(12))
            try:
                descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                    for key, value in sorted(env.items()):
                        if "\n" in value or "\r" in value:
                            raise ValueError("Environment contains a line break")
                        handle.write(f"{key}={value}\n")
                os.replace(temporary, env_path)
            finally:
                temporary.unlink(missing_ok=True)
            args.extend(["--env-file", str(env_path), image])
            self._docker(*args)
            return {"id": workload_id, "running": True}

    def stop(self, workload_id, *, remove=False, retire=False):
        workload_id = self._id(workload_id)
        if retire and not remove:
            raise ValueError("Retirement requires container removal")
        name = self._name(workload_id)
        with self.lock:
            container = self._inspect(name)
            if container is not None:
                self._checked(container, workload_id)
                if container["State"]["Running"]:
                    self._docker("stop", "--time", "20", name)
                if remove:
                    self._docker("rm", name)
            if retire:
                source = self.root / workload_id
                if source.exists() or source.is_symlink():
                    if source.is_symlink():
                        raise ValueError("Workload directory is not trusted")
                    target = self.root / ".retired"
                    target.mkdir(parents=True, exist_ok=True, mode=0o700)
                    source.rename(target / (workload_id + "-" + secrets.token_hex(12)))
        return {"id": workload_id, "running": False}

    def status(self, workload_id):
        workload_id = self._id(workload_id)
        container = self._inspect(self._name(workload_id))
        if container is None:
            return {"id": workload_id, "exists": False}
        self._checked(container, workload_id)
        return {"id": workload_id, "exists": True,
                "running": bool(container["State"]["Running"])}

    def request(self, payload):
        workload_id = self._id(payload.get("id"))
        method = payload.get("method")
        path = payload.get("path")
        if (method not in {"GET", "POST", "PUT", "DELETE"} or not isinstance(path, str)
                or not path.startswith("/") or path.startswith("//")
                or ".." in path.split("/") or "?" in path or "#" in path):
            raise ValueError("Invalid workload HTTP request")
        container = self._inspect(self._name(workload_id))
        if container is None:
            raise OSError("Workload not deployed")
        self._checked(container, workload_id)
        if not container["State"]["Running"]:
            raise OSError("Workload is stopped")
        ports = ((container.get("NetworkSettings") or {}).get("Ports") or {})
        bound = [item for entries in ports.values() for item in (entries or [])
                 if item.get("HostIp") == "127.0.0.1"]
        if len(bound) != 1:
            raise ValueError("Workload does not have one loopback HTTP port")
        headers = payload.get("headers") or {}
        if not isinstance(headers, dict):
            raise ValueError("Invalid headers")
        headers = {key: value for key, value in headers.items()
                   if key.lower() in {"authorization", _CONTRACT_HEADER.lower()}}
        timeout = min(max(int(payload.get("timeout", 120)), 1), 600)
        with httpx.Client(timeout=timeout, follow_redirects=False) as client:
            response = client.request(
                method, "http://127.0.0.1:" + bound[0]["HostPort"] + path,
                headers=headers, json=payload.get("json"), params=payload.get("params"),
            )
        if len(response.content) > 12 * 1024 * 1024:
            raise ValueError("Workload response exceeds relay size limit")
        try:
            body = response.json() if response.content else None
        except ValueError as exc:
            raise ValueError("Workload response is not JSON") from exc
        return {"status": response.status_code, "body": body,
                "headers": {_CONTRACT_HEADER: response.headers.get(_CONTRACT_HEADER, "")}}

    def execute(self, action, payload):
        if action == "ensure":
            return self.ensure(payload)
        if action == "stop":
            return self.stop(payload["id"], remove=payload.get("remove", False),
                             retire=payload.get("retire", False))
        if action == "status":
            return self.status(payload["id"])
        if action == "request":
            return self.request(payload)
        raise ValueError("Unsupported node operation")
