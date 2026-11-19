"""Engine MCP Research client used only by the trusted Platform task controller.

The Agent never receives an Engine grant or Engine origin. The existing HTTP
snapshot content endpoint is retained because MCP deliberately does not carry
large Parquet payloads. There is no automatic fallback to Engine REST tools.
"""
from __future__ import annotations

import json
import re

import httpx

from .direct_engine import DirectEngineResearchClient, DirectResearchError, _identifier

MCP_VERSION = "2025-11-25"
_MAX_MCP_RESULT = 1_048_576
_MAX_MCP_REQUEST = 512 * 1024
_SNAPSHOT = re.compile(r"/research/v1/snapshots/([A-Za-z0-9._:-]+)")
_BACKTEST = re.compile(r"/research/v1/backtests/([A-Za-z0-9._:-]+)(/cancel)?")


class EngineMCPResearchClient(DirectEngineResearchClient):
    """Translate the existing validated Platform research calls into Engine MCP."""

    def _request(self, method: str, path: str, payload: dict | None = None) -> dict:
        if method == "GET" and path == "/research/v1/datasets":
            tool, arguments = "list_datasets", {}
        elif method == "POST" and path == "/research/v1/snapshots":
            tool, arguments = "create_snapshot", payload
        elif method == "POST" and path == "/research/v1/backtests":
            tool, arguments = "submit_backtest", payload
        elif method == "POST" and path == "/research/v1/candidate-backtests":
            tool, arguments = "submit_candidate_backtest", payload
        elif method == "POST" and path == "/research/v1/python-candidate-backtests":
            tool, arguments = "submit_python_candidate_backtest", payload
        elif method == "GET" and (match := _SNAPSHOT.fullmatch(path)):
            tool, arguments = "get_snapshot", {"snapshot_id": _identifier(match.group(1))}
        elif (match := _BACKTEST.fullmatch(path)):
            identity = _identifier(match.group(1))
            if (method, match.group(2)) == ("GET", None):
                tool = "get_backtest"
            elif (method, match.group(2)) == ("POST", "/cancel"):
                tool = "cancel_backtest"
            else:
                raise ValueError("Unsupported Engine MCP research route")
            arguments = {"backtest_id": identity}
        else:
            raise ValueError("Unsupported Engine MCP research route")
        if not isinstance(arguments, dict):
            raise ValueError("Invalid research MCP arguments")
        try:
            request = json.dumps({
                "jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": tool, "arguments": arguments},
            }, allow_nan=False, separators=(",", ":")).encode("utf-8")
        except (TypeError, ValueError):
            raise ValueError("Invalid research MCP arguments") from None
        if len(request) > _MAX_MCP_REQUEST:
            raise ValueError("Research MCP request too large")
        try:
            with self.client.stream(
                "POST", "/research/mcp", content=request,
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json, text/event-stream",
                    "MCP-Protocol-Version": MCP_VERSION,
                },
            ) as response:
                if response.is_redirect or response.status_code >= 400:
                    raise DirectResearchError(
                        f"Engine MCP research rejected request: HTTP {response.status_code}"
                    )
                if response.headers.get("content-type", "").split(";", 1)[0].lower() != "application/json":
                    raise DirectResearchError("Invalid Engine MCP content type")
                data = bytearray()
                for chunk in response.iter_bytes():
                    if len(data) + len(chunk) > _MAX_MCP_RESULT:
                        raise DirectResearchError("Engine MCP result too large")
                    data.extend(chunk)
        except httpx.RequestError:
            raise DirectResearchError("Engine MCP research unavailable") from None
        try:
            rpc = json.loads(data)
        except (ValueError, UnicodeDecodeError):
            raise DirectResearchError("Invalid Engine MCP response") from None
        if (not isinstance(rpc, dict) or rpc.get("jsonrpc") != "2.0"
                or type(rpc.get("id")) is not int or rpc["id"] != 1
                or "error" in rpc):
            raise DirectResearchError("Engine MCP protocol error")
        result = rpc.get("result")
        if (not isinstance(result, dict) or result.get("isError") is True
                or not isinstance(result.get("structuredContent"), dict)):
            raise DirectResearchError("Engine MCP research operation rejected")
        return result["structuredContent"]
