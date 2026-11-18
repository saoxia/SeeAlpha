"""One resident Harness workload per user, routed by the existing Node Agent.

Harness remains single-user internally. User identity, credentials, instance
placement and permissions are always chosen by the trusted Platform, never
by the request body. A Harness workload never receives a trading or model key.
"""
from __future__ import annotations

import base64
import json
import os
import secrets
import stat
from pathlib import Path

import httpx

from ..node.docker import DockerRuntime
from .harness_client import HarnessClient, HARNESS_HEADER


class WorkloadTransport(httpx.BaseTransport):
    """Relay Harness HTTP through the generic local/remote node workload API."""

    def __init__(self, send, workload_id):
        self.send = send
        self.workload_id = workload_id

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        if request.url.host != "harness.internal":
            raise httpx.ConnectError("Unexpected Harness endpoint", request=request)
        try:
            payload = {
                "id": self.workload_id,
                "method": request.method,
                "path": request.url.path,
                "params": dict(request.url.params),
                "headers": {
                    "Authorization": request.headers["authorization"],
                    HARNESS_HEADER: request.headers[HARNESS_HEADER],
                },
            }
            if request.content:
                payload["json"] = json.loads(request.content)
            result = self.send("request", payload)
            headers = result.get("headers", {})
            if result.get("binary_b64") is not None:
                content = base64.b64decode(result["binary_b64"], validate=True)
                return httpx.Response(
                    result["status"], headers=headers, content=content, request=request,
                )
            return httpx.Response(
                result["status"], headers=headers, json=result.get("body"), request=request,
            )
        except (OSError, KeyError, TypeError, ValueError) as exc:
            raise httpx.ConnectError("Harness node transport unavailable", request=request) from None


class UserHarnessInstances:
    """Lazy per-user Harness provisioning using the generic DockerRuntime."""

    def __init__(self, *, root: Path, image: str, cpus: str, memory: str, hub=None):
        self.root = Path(root).expanduser().resolve()
        if Path(root).expanduser().is_symlink():
            raise ValueError("Harness root must not be a symlink")
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if stat.S_IMODE(self.root.stat().st_mode) & 0o077:
            raise ValueError("Harness root must be private (chmod 700)")
        if not image or image.endswith(":latest"):
            raise ValueError("Harness requires an explicitly versioned image")
        self.image = image
        self.cpus = cpus
        self.memory = memory
        self.hub = hub
        self.local = DockerRuntime(
            self.root / "workloads", allowed_images={image},
            max_containers=1000, max_cpus=64, max_memory="256g",
        )

    @staticmethod
    def _user_id(user_id):
        if type(user_id) is not int or user_id < 1:
            raise ValueError("Invalid user ID")
        return user_id

    @classmethod
    def _workload_id(cls, user_id):
        return f"harness-user-{cls._user_id(user_id)}"

    def _token(self, user_id):
        user_id = self._user_id(user_id)
        folder = self.root / "credentials"
        if folder.is_symlink():
            raise ValueError("Harness credential directory is not trusted")
        folder.mkdir(mode=0o700, exist_ok=True)
        if stat.S_IMODE(folder.stat().st_mode) & 0o077:
            raise ValueError("Harness credential directory must be private")
        path = folder / f"user-{user_id}.token"
        if path.is_symlink():
            raise ValueError("Harness credential file is not trusted")
        if not path.exists():
            try:
                descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                pass
            else:
                with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                    handle.write(secrets.token_urlsafe(48))
        if path.is_symlink() or stat.S_IMODE(path.stat().st_mode) & 0o077:
            raise ValueError("Harness credential file must be private")
        token = path.read_text(encoding="utf-8").strip()
        if len(token) < 32:
            raise ValueError("Harness credential is invalid")
        return token

    def _node(self, user_id):
        self._user_id(user_id)
        return self.hub.harness_assigned(user_id) if self.hub else "local"

    def _send(self, user_id, action, payload):
        node = self._node(user_id)
        if node == "local":
            return self.local.execute(action, payload)
        return self.hub.call(node, action, payload, timeout=90)

    def ensure(self, user_id):
        token = self._token(user_id)
        workload_id = self._workload_id(user_id)
        spec = {
            "id": workload_id,
            "image": self.image,
            "cpus": self.cpus,
            "memory": self.memory,
            "port": 8777,
            "mounts": {"state": "/app/state"},
            "env": {
                "HARNESS_API_TOKEN": token,
                "HARNESS_STATE_DIR": "/app/state",
                "HOME": "/app/state/home",
                "XDG_CACHE_HOME": "/app/state/cache",
                "XDG_CONFIG_HOME": "/app/state/config",
            },
        }
        self._send(user_id, "ensure", spec)
        return token

    def client(self, user_id):
        token = self.ensure(user_id)
        transport = WorkloadTransport(
            lambda action, payload: self._send(user_id, action, payload),
            self._workload_id(user_id),
        )
        return HarnessClient("http://harness.internal", token, transport=transport)
