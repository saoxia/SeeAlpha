"""Outbound-only Node Agent. No inbound port or SeeAlpha business runtime required."""
from __future__ import annotations

import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlsplit

import httpx

from .docker import DockerRuntime

_LOG = logging.getLogger(__name__)


class NodeAgent:
    def __init__(self, *, server, node_id, token, runtime, transport=None):
        url = urlsplit(server)
        if (not node_id or not token or len(token) < 32 or url.username or url.password
                or url.query or url.fragment or url.path not in {"", "/"}
                or not (url.scheme == "https" or
                        (url.scheme == "http" and url.hostname in {"localhost", "127.0.0.1"}))):
            raise ValueError("Agent requires an HTTPS Platform URL and private node credentials")
        self.server = server.rstrip("/")
        self.node_id = node_id
        self.headers = {"X-Node-Id": node_id, "Authorization": f"Bearer {token}"}
        self.runtime = runtime
        self.transport = transport

    def _execute(self, command):
        try:
            result = self.runtime.execute(command["action"], command["payload"])
            message = {"id": command["id"], "ok": True, "result": result}
        except (ValueError, OSError, KeyError, httpx.HTTPError) as exc:
            _LOG.warning("Node workload operation failed: %s", type(exc).__name__)
            message = {"id": command["id"], "ok": False, "result": None}
        # Result delivery failures do not imply command rollback. Platform marks
        # timed-out outcomes unconfirmed; it must not blindly replay trade commands.
        try:
            with httpx.Client(timeout=25, transport=self.transport) as client:
                response = client.post(self.server + "/api/node-agent/result",
                                       headers=self.headers, json=message)
                response.raise_for_status()
        except httpx.HTTPError:
            _LOG.warning("Node result delivery failed; command outcome may be unconfirmed")

    def poll_once(self):
        with httpx.Client(timeout=25, transport=self.transport) as client:
            response = client.post(self.server + "/api/node-agent/poll", headers=self.headers)
            response.raise_for_status()
            return response.json().get("command")

    def run(self):
        with ThreadPoolExecutor(max_workers=4, thread_name_prefix="seealpha-node") as workers:
            while True:
                try:
                    command = self.poll_once()
                    if command is not None:
                        workers.submit(self._execute, command)
                except (httpx.HTTPError, ValueError) as exc:
                    _LOG.warning("Node connection unavailable: %s", type(exc).__name__)
                    time.sleep(3)


def run_agent():
    images = [x.strip() for x in os.getenv("NODE_ALLOWED_IMAGES", "").split(",") if x.strip()]
    runtime = DockerRuntime(
        os.getenv("NODE_ROOT", ".local/node-workloads"),
        allowed_images=images,
        max_containers=int(os.getenv("NODE_MAX_CONTAINERS", "8")),
        max_cpus=os.getenv("NODE_MAX_CPUS", "8"),
        max_memory=os.getenv("NODE_MAX_MEMORY", "16g"),
    )
    agent = NodeAgent(
        server=os.environ["NODE_PLATFORM_URL"],
        node_id=os.environ["NODE_ID"],
        token=os.environ["NODE_TOKEN"],
        runtime=runtime,
    )
    agent.run()
