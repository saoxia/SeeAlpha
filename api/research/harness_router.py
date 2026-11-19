"""Authenticated Platform bridge to single-user or user-dedicated Harness services.

Harness has no production Engine research grants or trading credentials.
"""
from __future__ import annotations

from contextlib import contextmanager
from hashlib import sha256
from pathlib import PurePath
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from starlette.responses import Response

from ..auth.dependencies import CurrentUser
from .harness_client import HarnessClient, HarnessRejected, HarnessUnavailable
from .task_access import allowed_scope, engine_connection

router = APIRouter(prefix="/harness", tags=["research-harness"])


class PromptBody(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    prompt: str = Field(min_length=1, max_length=20_000)
    queue_if_busy: bool = True
    research_tenant_id: int | None = Field(default=None, gt=0)
    research_account_id: str | None = Field(default=None, max_length=40)
    research_dataset_ref: str | None = Field(default=None, max_length=128)


class ImportArtifactBody(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    source_file: str = Field(min_length=1, max_length=140)


def _require_harness_owner(request: Request, user: CurrentUser) -> None:
    # A resident Harness has one shared UID, model key, and task history. Only
    # Single mode has one explicitly designated owner; per-user mode maps each
    # authenticated non-admin user to a separate resident Harness.
    settings = request.app.state.settings
    if not request.app.state.user_resources.enabled(user.id, "harness"):
        raise HTTPException(403, "管理员尚未为此用户分配 AI Harness。")
    if settings.harness_deployment == "per_user":
        if user.username.casefold() == settings.admin_username.casefold():
            raise HTTPException(403, "管理员不能运行普通用户的研究任务。")
        return
    owner = settings.harness_owner_username
    if not owner or owner.casefold() == settings.admin_username.casefold():
        raise HTTPException(503, "必须将 HARNESS_OWNER_USERNAME 显式设置为独立普通用户。")
    if user.username.casefold() != owner.casefold():
        raise HTTPException(403, "当前账户无权访问此单用户 Harness 运行实例。")


@contextmanager
def _client(request: Request, user: CurrentUser):
    settings = request.app.state.settings
    if not settings.harness_enabled:
        raise HTTPException(503, "Harness 尚未启用。")
    try:
        if settings.harness_deployment == "per_user":
            allocation = request.app.state.user_resources.get(user.id)["harness"]
            if allocation["cpus"] is None and allocation["memory"] is None:
                client = request.app.state.harness_instances.client(user.id)
            else:
                client = request.app.state.harness_instances.client(
                    user.id, cpus=allocation["cpus"], memory=allocation["memory"]
                )
        else:
            client = HarnessClient(settings.harness_url, settings.harness_token)
    except (ValueError, OSError):
        raise HTTPException(503, "Harness 实例暂时不可用或配置不完整。") from None
    try:
        yield client
    except HarnessRejected as error:
        messages = {404: "Harness 任务不存在。", 409: "Harness 正忙或任务状态不允许此操作。",
                    429: "Harness 任务队列已满。"}
        raise HTTPException(error.status, messages[error.status]) from None
    except (HarnessUnavailable, ValueError):
        raise HTTPException(503, "Harness 不可用或协议不兼容。") from None
    finally:
        client.close()


@router.get("/status")
def harness_status(request: Request, user: CurrentUser):
    _require_harness_owner(request, user)
    if not request.app.state.settings.harness_enabled:
        return {"enabled": False, "available": False, "runtime_configured": False}
    try:
        with _client(request, user) as client:
            state = client.health()
        return {"enabled": True, "available": True, **state}
    except HTTPException:
        return {"enabled": True, "available": False, "runtime_configured": False}


def _requested_research(request: Request, body: PromptBody, user: CurrentUser):
    selection = (
        body.research_tenant_id, body.research_account_id, body.research_dataset_ref,
    )
    if all(value is None for value in selection):
        return None
    if any(value is None for value in selection):
        raise HTTPException(422, "Research needs a development tenant, account and dataset")
    if request.app.state.research_leases is None:
        raise HTTPException(503, "Development research is not enabled")
    tenant_id, account_id, dataset = selection
    if not allowed_scope(request.app, user.id, tenant_id, account_id, dataset):
        raise HTTPException(403, "Development research scope not authorized")
    engine_connection(request.app, tenant_id)
    origin = request.app.state.settings.research_platform_origin
    if not origin:
        raise HTTPException(503, "Research Platform origin is not configured")
    return tenant_id, account_id, dataset, origin


def _research_access(request: Request, task_id: str, user_id: int,
                     tenant_id: int, account_id: str, dataset: str, origin: str,
                     turn: int):
    token, expires_at = request.app.state.research_leases.issue(
        task_id=task_id, owner_id=user_id, tenant_id=tenant_id,
        account_id=account_id, dataset_ref=dataset, turn=turn,
    )
    return {"origin": origin, "token": token, "expires_at": expires_at}


@router.post("/tasks", status_code=202)
def start_harness_task(request: Request, body: PromptBody, user: CurrentUser):
    _require_harness_owner(request, user)
    scope = _requested_research(request, body, user)
    if scope is None:
        with _client(request, user) as client:
            return client.create(body.prompt, queue_if_busy=body.queue_if_busy)
    task_id = str(uuid4())
    tenant, account, dataset, origin = scope
    access = _research_access(
        request, task_id, user.id, tenant, account, dataset, origin, 1,
    )
    try:
        with _client(request, user) as client:
            return client.create(
                body.prompt, task_id=task_id, research_access=access, queue_if_busy=False,
            )
    except Exception:
        request.app.state.research_leases.revoke(task_id)
        raise


@router.get("/tasks")
def harness_history(request: Request, user: CurrentUser):
    _require_harness_owner(request, user)
    with _client(request, user) as client:
        return {"tasks": client.recent_tasks()}


@router.get("/tasks/{task_id}/timeline")
def harness_timeline(request: Request, task_id: str, user: CurrentUser,
                     after: int = 0):
    _require_harness_owner(request, user)
    with _client(request, user) as client:
        return client.timeline(task_id, after=after)


@router.post("/tasks/{task_id}/artifacts/import", status_code=201)
def import_harness_artifact(request: Request, task_id: str,
                            body: ImportArtifactBody, user: CurrentUser):
    _require_harness_owner(request, user)
    with _client(request, user) as client:
        return client.import_artifact(task_id, body.source_file)


@router.get("/tasks/{task_id}")
def harness_task(request: Request, task_id: str, user: CurrentUser):
    _require_harness_owner(request, user)
    with _client(request, user) as client:
        return client.get(task_id)


@router.post("/tasks/{task_id}/continue", status_code=202)
def continue_harness_task(request: Request, task_id: str, body: PromptBody,
                          user: CurrentUser):
    _require_harness_owner(request, user)
    leases = request.app.state.research_leases
    prior = leases.owner_scope(task_id, user.id) if leases is not None else None
    with _client(request, user) as client:
        if prior is None:
            return client.continue_task(task_id, body.prompt, queue_if_busy=body.queue_if_busy)
        task = client.get(task_id)
        if task["status"] != "completed":
            raise HTTPException(409, "Research continuation requires a completed task")
        scope = (
            prior["tenant_id"], prior["account_id"], prior["dataset_ref"],
        )
        if not allowed_scope(request.app, user.id, *scope):
            raise HTTPException(403, "Research scope no longer authorized")
        engine_connection(request.app, prior["tenant_id"])
        origin = request.app.state.settings.research_platform_origin
        if not origin:
            raise HTTPException(503, "Research Platform origin unavailable")
        access = _research_access(
            request, task_id, user.id, *scope, origin, task["turn"] + 1,
        )
        try:
            return client.continue_task(
                task_id, body.prompt, queue_if_busy=False, research_access=access,
            )
        except Exception:
            leases.revoke(task_id)
            raise


@router.post("/tasks/{task_id}/cancel", status_code=202)
def cancel_harness_task(request: Request, task_id: str, user: CurrentUser):
    _require_harness_owner(request, user)
    if request.app.state.research_leases is not None:
        prior = request.app.state.research_leases.owner_scope(task_id, user.id)
        if prior is not None:
            request.app.state.research_leases.revoke(task_id)
    with _client(request, user) as client:
        return client.cancel(task_id)


@router.get("/tasks/{task_id}/artifacts")
def harness_artifacts(request: Request, task_id: str, user: CurrentUser):
    _require_harness_owner(request, user)
    with _client(request, user) as client:
        return {"artifacts": client.artifacts(task_id)}


@router.get("/tasks/{task_id}/artifacts/{artifact_id}")
def download_harness_artifact(request: Request, task_id: str,
                              artifact_id: str, user: CurrentUser):
    _require_harness_owner(request, user)
    with _client(request, user) as client:
        items = client.artifacts(task_id)
        item = next((x for x in items if x["artifact_id"] == artifact_id), None)
        if item is None:
            raise HTTPException(404, "成果文件不存在。")
        raw = client.download_artifact(task_id, artifact_id)
        if ((type(item.get("size")) is int and item["size"] != len(raw))
                or (isinstance(item.get("sha256"), str)
                    and sha256(raw).hexdigest() != item["sha256"])):
            raise HTTPException(503, "成果文件完整性检查失败。")
        # Always a forced download, even for Markdown/HTML. No active user
        # content is served as trusted same-origin HTML or JavaScript.
        extension = PurePath(str(item.get("filename") or "")).suffix.lower()
        if extension not in {".md", ".txt", ".csv", ".json", ".png", ".jpg",
                             ".jpeg", ".pdf", ".py", ".parquet", ".bin"}:
            extension = ".bin"
        return Response(raw, media_type="application/octet-stream", headers={
            "Content-Disposition": f'attachment; filename="{artifact_id}{extension}"',
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "no-store",
        })
