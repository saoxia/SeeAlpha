"""Authenticated, fixed-function research commands for one active Harness task."""
from __future__ import annotations

import json
from types import SimpleNamespace

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse, Response

from ..research.harness_client import HarnessUnavailable, validate_task_id
from ..research.harness_router import _client, _require_harness_owner
from .direct_engine import DirectResearchError
from .task_access import OPERATIONS, research_command

router = APIRouter(prefix="/research/tasks", tags=["development-research"])


class CommandBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    arguments: dict = Field(default_factory=dict)


def _owner_harness(request: Request, owner_id: int):
    record = request.app.state.repository.get_user_by_id(owner_id)
    if record is None:
        raise HTTPException(403, "Research task owner is no longer available")
    actor = SimpleNamespace(id=owner_id, username=record["username"])
    _require_harness_owner(request, actor)
    return actor


def _authorized(request: Request, task_id: str, operation: str):
    validate_task_id(task_id)
    leases = request.app.state.research_leases
    if leases is None:
        raise HTTPException(503, "Research proxy is disabled")
    if operation not in OPERATIONS:
        raise HTTPException(404, "Unknown research command")
    bearer = request.headers.get("authorization", "")
    scheme, _, token = bearer.partition(" ")
    if scheme.lower() != "bearer" or len(token) < 40 or len(token) > 256:
        raise HTTPException(401, "Research task authentication required")
    # Do not trust task ID/turn/tenant supplied in the command payload.
    # First check the authenticated Harness task, then atomically spend budget.
    row = leases.authenticated(task_id, token)
    actor = _owner_harness(request, row["owner_id"])
    try:
        with _client(request, actor) as harness:
            task = harness.get(task_id)
    except (HarnessUnavailable, ValueError):
        raise HTTPException(503, "Harness task state unavailable") from None
    if task["status"] not in {"starting", "running"}:
        raise HTTPException(409, "Research task is not running")
    return leases.consume(task_id, token, task["turn"], operation)


@router.post("/{task_id}/commands/{operation}")
def execute(request: Request, task_id: str, operation: str, body: CommandBody):
    lease = _authorized(request, task_id, operation)
    try:
        result = research_command(request.app, {**lease, "task_id": task_id},
                                  operation, body.arguments)
    except (DirectResearchError, OSError):
        raise HTTPException(503, "Development Engine research unavailable") from None
    except (ValueError, TypeError):
        raise HTTPException(422, "Invalid research arguments") from None
    if operation == "snapshot_fetch":
        return Response(
            result, media_type="application/octet-stream",
            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
        )
    return result


# MCP is exposed only through this task-capability route; no Engine research grant
# or Engine origin crosses into the Agent process. Candidate archives and Parquet
# downloads retain the existing fixed-function Platform commands.
_MCP_VERSION = "2025-11-25"
_MCP_TO_OPERATION = {
    "list_datasets": "datasets",
    "create_snapshot": "snapshot_create",
    "get_snapshot": "snapshot_get",
    "submit_backtest": "backtest_submit",
    "get_backtest": "backtest_get",
    "cancel_backtest": "backtest_cancel",
    "submit_candidate_backtest": "candidate_submit",
    "submit_python_candidate_backtest": "python_candidate_submit",
}
_MCP_ARGUMENTS = {
    "list_datasets": (),
    "create_snapshot": ("start", "end"),
    "get_snapshot": ("snapshot_id",),
    "submit_backtest": (
        "start", "end", "starting_balance", "quote_currency", "idempotency_key",
    ),
    "get_backtest": ("backtest_id",),
    "cancel_backtest": ("backtest_id",),
    "submit_candidate_backtest": (
        "candidate_id", "start", "end", "starting_balance",
        "quote_currency", "idempotency_key",
    ),
    "submit_python_candidate_backtest": (
        "candidate_id", "start", "end", "starting_balance",
        "quote_currency", "idempotency_key",
    ),
}


def _mcp_reply(identity, result: dict) -> JSONResponse:
    return JSONResponse({"jsonrpc": "2.0", "id": identity, "result": result})


def _mcp_error(identity, code: int, message: str) -> JSONResponse:
    return JSONResponse({
        "jsonrpc": "2.0", "id": identity,
        "error": {"code": code, "message": message},
    })


def _mcp_tools(request: Request) -> list[dict]:
    tools = []
    for name, fields in _MCP_ARGUMENTS.items():
        if (name == "submit_python_candidate_backtest"
                and not request.app.state.settings.research_python_enabled):
            continue
        title = name.replace("_", " ")
        if name in {"submit_candidate_backtest", "submit_python_candidate_backtest"}:
            title += " using a candidate ID registered by the Platform research CLI"
        properties = {field: {"type": "string", "minLength": 1}
                      for field in fields}
        tools.append({
            "name": name,
            "description": "Development-only: " + title + ". Never executes live trades.",
            "inputSchema": {
                "type": "object", "properties": properties,
                "required": list(fields), "additionalProperties": False,
            },
        })
    return tools


@router.post("/{task_id}/mcp")
async def task_mcp(request: Request, task_id: str):
    """Stateless JSON Streamable HTTP adapter with per-message task authorization."""
    if request.headers.get("origin") is not None:
        raise HTTPException(403, "Browser MCP requests are not accepted")
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
        raise HTTPException(415, "MCP requires application/json")
    if "application/json" not in request.headers.get("accept", "*/*").lower() and (
        "*/*" not in request.headers.get("accept", "*/*")
    ):
        raise HTTPException(406, "MCP JSON responses required")
    if request.headers.get("mcp-protocol-version") not in {None, _MCP_VERSION}:
        raise HTTPException(400, "Unsupported MCP protocol version")
    data = bytearray()
    async for chunk in request.stream():
        if len(data) + len(chunk) > 512 * 1024:
            raise HTTPException(413, "MCP request too large")
        data.extend(chunk)
    try:
        message = json.loads(data)
    except (UnicodeError, ValueError):
        return _mcp_error(None, -32700, "Parse error")
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
        return _mcp_error(None, -32600, "Invalid Request")
    method, identity = message.get("method"), message.get("id")
    if not isinstance(method, str) or not method:
        return _mcp_error(None, -32600, "Invalid Request")
    if "id" not in message:
        if method != "notifications/initialized":
            return _mcp_error(None, -32600, "Invalid Request")
        await run_in_threadpool(_authorized, request, task_id, "datasets")
        return Response(status_code=202)
    if type(identity) not in {int, str}:
        return _mcp_error(None, -32600, "Invalid Request")
    params = message.get("params", {})
    if not isinstance(params, dict):
        return _mcp_error(identity, -32602, "Invalid params")
    if method == "initialize":
        if not isinstance(params.get("protocolVersion"), str):
            return _mcp_error(identity, -32602, "Invalid initialize params")
        await run_in_threadpool(_authorized, request, task_id, "datasets")
        return _mcp_reply(identity, {
            "protocolVersion": _MCP_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "seealpha-task-research", "version": "1.0.0"},
            "instructions": "Only task-authorized development research. "
                            "Use the Platform CLI for local candidate registration "
                            "and verified Parquet downloads. No live trading.",
        })
    if method == "ping":
        await run_in_threadpool(_authorized, request, task_id, "datasets")
        return _mcp_reply(identity, {})
    if method == "tools/list":
        if params.get("cursor") is not None:
            return _mcp_error(identity, -32602, "Invalid tools cursor")
        await run_in_threadpool(_authorized, request, task_id, "datasets")
        return _mcp_reply(identity, {"tools": _mcp_tools(request)})
    if method != "tools/call":
        return _mcp_error(identity, -32601, "Method not found")
    name, arguments = params.get("name"), params.get("arguments", {})
    if not isinstance(name, str) or name not in _MCP_TO_OPERATION or not isinstance(arguments, dict):
        return _mcp_error(identity, -32602, "Unknown tool or invalid arguments")
    if (name == "submit_python_candidate_backtest"
            and not request.app.state.settings.research_python_enabled):
        return _mcp_error(identity, -32602, "Tool unavailable")
    if set(arguments) != set(_MCP_ARGUMENTS[name]):
        return _mcp_reply(identity, {
            "isError": True, "content": [
                {"type": "text", "text": "Invalid research tool arguments"},
            ],
        })
    operation = _MCP_TO_OPERATION[name]
    lease = await run_in_threadpool(_authorized, request, task_id, operation)
    try:
        result = await run_in_threadpool(
            research_command, request.app, {**lease, "task_id": task_id},
            operation, arguments,
        )
        if not isinstance(result, dict):
            raise TypeError("Invalid research result")
        serialized = json.dumps(result, ensure_ascii=False, allow_nan=False)
        if len(serialized.encode("utf-8")) > 1_048_576:
            raise ValueError("Research result too large")
    except (DirectResearchError, OSError, ValueError, TypeError, HTTPException):
        return _mcp_reply(identity, {
            "isError": True, "content": [
                {"type": "text", "text": "Development research operation rejected"},
            ],
        })
    return _mcp_reply(identity, {
        "content": [{"type": "text", "text": serialized}],
        "structuredContent": result,
    })
