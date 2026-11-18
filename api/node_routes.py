"""Authenticated Node Agent transport and admin-only node status."""
from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from .auth.dependencies import AdminUser

router = APIRouter(tags=["nodes"])


def hub(request: Request):
    current = getattr(request.app.state, "node_hub", None)
    if current is None:
        raise HTTPException(503, "Node Agent network is not enabled")
    return current


class NodeResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[0-9a-f]{32}$")
    ok: bool
    result: dict | None = None


@router.post("/node-agent/poll")
def node_poll(request: Request, x_node_id: str = Header(default=""),
              authorization: str = Header(default="")):
    return hub(request).poll(x_node_id, authorization)


@router.post("/node-agent/result")
def node_result(request: Request, payload: NodeResult,
                x_node_id: str = Header(default=""),
                authorization: str = Header(default="")):
    return hub(request).complete(x_node_id, authorization, payload.model_dump())


@router.get("/nodes")
def list_nodes(request: Request, _: AdminUser):
    return hub(request).describe()
