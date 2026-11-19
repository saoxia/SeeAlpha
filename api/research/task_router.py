"""Authenticated, fixed-function research commands for one active Harness task."""
from __future__ import annotations

from types import SimpleNamespace

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from starlette.responses import Response

from ..research.harness_client import HarnessUnavailable, validate_task_id
from ..research.harness_router import _client, _require_harness_owner
from .direct_engine import DirectResearchError
from .task_access import OPERATIONS, allowed_scope, research_command

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
