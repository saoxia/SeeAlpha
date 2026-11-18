"""Authenticated Platform bridge to single-user or user-dedicated Harness services.

Harness has no production Engine research grants or trading credentials.
"""
from __future__ import annotations

from contextlib import contextmanager
from hashlib import sha256
from pathlib import PurePath

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from starlette.responses import Response

from ..auth.dependencies import CurrentUser
from .harness_client import HarnessClient, HarnessRejected, HarnessUnavailable

router = APIRouter(prefix="/harness", tags=["research-harness"])


class PromptBody(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    prompt: str = Field(min_length=1, max_length=20_000)
    queue_if_busy: bool = True


class ImportArtifactBody(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    source_file: str = Field(min_length=1, max_length=140)


def _require_harness_owner(request: Request, user: CurrentUser) -> None:
    # A resident Harness has one shared UID, model key, and task history. Only
    # Single mode has one explicitly designated owner; per-user mode maps each
    # authenticated non-admin user to a separate resident Harness.
    settings = request.app.state.settings
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
            client = request.app.state.harness_instances.client(user.id)
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


@router.post("/tasks", status_code=202)
def start_harness_task(request: Request, body: PromptBody, user: CurrentUser):
    _require_harness_owner(request, user)
    with _client(request, user) as client:
        return client.create(body.prompt, queue_if_busy=body.queue_if_busy)


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
    with _client(request, user) as client:
        return client.continue_task(task_id, body.prompt, queue_if_busy=body.queue_if_busy)


@router.post("/tasks/{task_id}/cancel", status_code=202)
def cancel_harness_task(request: Request, task_id: str, user: CurrentUser):
    _require_harness_owner(request, user)
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
