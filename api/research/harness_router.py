"""Admin-only, opt-in development bridge to a single resident Harness.

Not a multi-tenant research control plane. No Engine research grants or trading
credentials are delivered to the Agent. Do not enable for untrusted customers.
"""
from __future__ import annotations

from contextlib import contextmanager
from hashlib import sha256
from pathlib import PurePath

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from starlette.responses import Response

from ..auth.dependencies import CurrentContext, actor_is_admin
from .harness_client import HarnessClient, HarnessRejected, HarnessUnavailable

router = APIRouter(prefix="/harness", tags=["research-harness"])


class PromptBody(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    prompt: str = Field(min_length=1, max_length=20_000)
    queue_if_busy: bool = True


def _require_developer(request: Request, context: CurrentContext) -> None:
    # Never permit the administrator to run arbitrary Agent code while acting
    # as another user. Current local Harness is one-user/single-UID only.
    if context.impersonating or not actor_is_admin(request, context.actor.username):
        raise HTTPException(403, "Harness 仅对未代入其他用户的管理员开放。")


@contextmanager
def _client(request: Request):
    settings = request.app.state.settings
    if not settings.harness_enabled:
        raise HTTPException(503, "Harness 尚未启用。")
    try:
        client = HarnessClient(settings.harness_url, settings.harness_token)
    except ValueError:
        raise HTTPException(503, "Harness 连接配置不完整。") from None
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
def harness_status(request: Request, context: CurrentContext):
    _require_developer(request, context)
    if not request.app.state.settings.harness_enabled:
        return {"enabled": False, "available": False, "runtime_configured": False}
    try:
        with _client(request) as client:
            state = client.health()
        return {"enabled": True, "available": True, **state}
    except HTTPException:
        return {"enabled": True, "available": False, "runtime_configured": False}


@router.post("/tasks", status_code=202)
def start_harness_task(request: Request, body: PromptBody, context: CurrentContext):
    _require_developer(request, context)
    with _client(request) as client:
        return client.create(body.prompt, queue_if_busy=body.queue_if_busy)


@router.get("/tasks/{task_id}")
def harness_task(request: Request, task_id: str, context: CurrentContext):
    _require_developer(request, context)
    with _client(request) as client:
        return client.get(task_id)


@router.post("/tasks/{task_id}/continue", status_code=202)
def continue_harness_task(request: Request, task_id: str, body: PromptBody,
                          context: CurrentContext):
    _require_developer(request, context)
    with _client(request) as client:
        return client.continue_task(task_id, body.prompt, queue_if_busy=body.queue_if_busy)


@router.post("/tasks/{task_id}/cancel", status_code=202)
def cancel_harness_task(request: Request, task_id: str, context: CurrentContext):
    _require_developer(request, context)
    with _client(request) as client:
        return client.cancel(task_id)


@router.get("/tasks/{task_id}/artifacts")
def harness_artifacts(request: Request, task_id: str, context: CurrentContext):
    _require_developer(request, context)
    with _client(request) as client:
        return {"artifacts": client.artifacts(task_id)}


@router.get("/tasks/{task_id}/artifacts/{artifact_id}")
def download_harness_artifact(request: Request, task_id: str,
                              artifact_id: str, context: CurrentContext):
    _require_developer(request, context)
    with _client(request) as client:
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
