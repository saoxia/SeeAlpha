"""Platform-side broker for outbound-only Node Agents.

First phase intentionally uses one Platform API process. The broker holds only
transient commands; tenant placement is explicit and loaded from a private file.
"""
from __future__ import annotations

import hmac
import json
import queue
import re
import stat
import threading
import time
from concurrent.futures import Future, TimeoutError
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException

_NODE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_TENANT = re.compile(r"^[1-9][0-9]*$")


class NodeHub:
    def __init__(self, config_file: Path):
        path = Path(config_file).expanduser()
        if path.is_symlink() or not path.is_file() or stat.S_IMODE(path.stat().st_mode) & 0o077:
            raise ValueError("NODE_CONFIG_FILE must be a regular private file (chmod 600)")
        settings = json.loads(path.read_text(encoding="utf-8"))
        nodes = settings.get("nodes", {})
        assignments = settings.get("tenant_nodes", {})
        if (not isinstance(nodes, dict) or not isinstance(assignments, dict)
                or any(not _NODE.fullmatch(n) or not isinstance(t, str) or len(t) < 32
                       for n, t in nodes.items())
                or any(not _TENANT.fullmatch(t) or (n != "local" and n not in nodes)
                       for t, n in assignments.items())):
            raise ValueError("Invalid node configuration or tenant placement")
        self.tokens = nodes
        self.tenant_nodes = assignments
        self.queues = {node: queue.Queue(maxsize=32) for node in nodes}
        self.last_seen = {}
        self.pending = {}
        self.lock = threading.RLock()

    def assigned(self, tenant_id):
        return self.tenant_nodes.get(str(tenant_id), "local")

    def authenticate(self, node_id, authorization):
        token = self.tokens.get(node_id)
        if token is None or not hmac.compare_digest(authorization, f"Bearer {token}"):
            raise HTTPException(401, "Node authentication failed")
        with self.lock:
            self.last_seen[node_id] = time.monotonic()

    def poll(self, node_id, authorization):
        self.authenticate(node_id, authorization)
        deadline = time.monotonic() + 12
        while True:
            try:
                command = self.queues[node_id].get(
                    timeout=max(0.01, deadline - time.monotonic()),
                )
            except queue.Empty:
                return {"command": None}
            with self.lock:
                if command["id"] in self.pending:
                    return {"command": command}
            if time.monotonic() >= deadline:
                return {"command": None}

    def complete(self, node_id, authorization, result):
        self.authenticate(node_id, authorization)
        identity = result.get("id")
        if not isinstance(identity, str) or not isinstance(result.get("ok"), bool):
            raise HTTPException(422, "Invalid node response")
        with self.lock:
            pair = self.pending.get(identity)
            if pair is None or pair[0] != node_id:
                # A timed-out request may finish late. Never re-assign its result.
                return {"accepted": False}
            future = pair[1]
            if not future.done():
                future.set_result(result)
        return {"accepted": True}

    def call(self, node_id, action, payload, *, timeout=120):
        if node_id not in self.queues:
            raise OSError("Unknown node")
        with self.lock:
            seen = self.last_seen.get(node_id, 0)
            if time.monotonic() - seen > 35:
                raise OSError("Node is offline")
            identity = uuid4().hex
            future = Future()
            self.pending[identity] = (node_id, future)
            try:
                self.queues[node_id].put_nowait({
                    "id": identity, "action": action, "payload": payload,
                })
            except queue.Full as exc:
                self.pending.pop(identity, None)
                raise OSError("Node command queue is full") from exc
        try:
            answer = future.result(timeout=timeout)
            if not answer["ok"]:
                raise OSError("Node operation rejected or failed")
            return answer["result"]
        except TimeoutError as exc:
            # Outcome unknown: never retry a potentially non-idempotent command.
            raise OSError("Node request timed out; outcome is unconfirmed") from exc
        finally:
            with self.lock:
                self.pending.pop(identity, None)

    def describe(self):
        with self.lock:
            return {
                "nodes": [
                    {"id": node, "online": time.monotonic() - self.last_seen.get(node, 0) <= 35}
                    for node in sorted(self.tokens)
                ],
                "tenant_nodes": dict(self.tenant_nodes),
            }
