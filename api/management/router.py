"""Authenticated checks, backtests, reports and job logs."""

import asyncio
import json
import mimetypes
import os
import stat
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from starlette.responses import Response, StreamingResponse

from ..auth.dependencies import AdminUser, CurrentUser
from ..db.errors import StorageUnavailable
from .artifacts import artifact_path
from .jobs import BusyError, NotOwnerError

router = APIRouter(prefix="/management", tags=["management"])

_REPORT_POLICY = (
    "sandbox allow-scripts allow-downloads; default-src 'none'; "
    "script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data: blob:; "
    "font-src data:; connect-src 'none'"
)
_MAX_BODY = 4096


def _service(request: Request):
    service = getattr(request.app.state, "management", None)
    if service is None:
        raise HTTPException(status_code=503, detail="管理任务未启用。")
    return service


async def _json_object(request: Request, allowed: set[str]):
    content_type = request.headers.get("content-type", "")
    if content_type.split(";", 1)[0].strip().lower() != "application/json":
        raise HTTPException(status_code=415, detail="请求必须为 JSON")
    raw = await request.body()
    if not raw or len(raw) > _MAX_BODY:
        raise HTTPException(status_code=400, detail="请求长度无效")
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise HTTPException(status_code=400, detail="参数无效") from exc
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="请求必须为对象")
    if not set(data) <= allowed:
        raise HTTPException(status_code=400, detail="请求包含未知字段")
    return data


def _job_error(exc):
    if isinstance(exc, NotOwnerError):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, BusyError):
        return HTTPException(status_code=409, detail="已有任务运行，请等待完成或取消后再试")
    if isinstance(exc, KeyError):
        return HTTPException(status_code=404, detail="任务不存在")
    if isinstance(exc, (ValueError, TypeError, UnicodeError)):
        return HTTPException(status_code=400, detail=str(exc) or "参数无效")
    if isinstance(exc, (OSError, StorageUnavailable)):
        return HTTPException(status_code=503, detail="暂时无法执行，请稍后重试")
    if isinstance(exc, RuntimeError):
        return HTTPException(status_code=503, detail="管理任务未启用。")
    return exc


@router.get("/state")
def management_state(request: Request, _: CurrentUser):
    try:
        return _service(request).state()
    except (OSError, StorageUnavailable) as exc:
        raise HTTPException(status_code=503, detail="暂时无法读取，请稍后重试") from exc


@router.get("/jobs/{identity}")
def management_job(identity: str, request: Request, _: CurrentUser):
    try:
        return _service(request).jobs.get_job(identity)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="任务不存在") from exc
    except (OSError, StorageUnavailable) as exc:
        raise HTTPException(status_code=503, detail="暂时无法读取，请稍后重试") from exc


@router.post("/jobs", status_code=202)
async def start_management_job(request: Request, _: AdminUser):
    data = await _json_object(request, {"action", "parameters"})
    service = _service(request)
    parameters = data.get("parameters", {})
    try:
        job = await asyncio.to_thread(service.jobs.start, data.get("action"), parameters)
    except (
        BusyError,
        NotOwnerError,
        KeyError,
        ValueError,
        TypeError,
        UnicodeError,
        OSError,
        RuntimeError,
    ) as exc:
        raise _job_error(exc) from exc
    return {"job": job}


@router.post("/jobs/{identity}/cancel")
async def cancel_management_job(identity: str, request: Request, _: AdminUser):
    await _json_object(request, set())
    service = _service(request)
    try:
        job = await asyncio.to_thread(service.jobs.cancel, identity)
    except (
        BusyError,
        NotOwnerError,
        KeyError,
        ValueError,
        TypeError,
        UnicodeError,
        OSError,
        RuntimeError,
    ) as exc:
        raise _job_error(exc) from exc
    return {"job": job}


def _artifact_response(path: Path, *, head: bool):
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as exc:
        raise HTTPException(status_code=404, detail="文件或任务不存在") from exc
    source = os.fdopen(descriptor, "rb")
    try:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise FileNotFoundError
        media = mimetypes.guess_type(path.name)[0] or "text/plain"
        if media.startswith("text/") or media == "application/json":
            media += "; charset=utf-8"
        headers = {
            "Content-Length": str(info.st_size),
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer",
            "X-Frame-Options": "DENY",
            "Content-Security-Policy": _REPORT_POLICY,
        }
        if head:
            return Response(status_code=200, media_type=media, headers=headers)
        size = info.st_size

        def chunks(handle, left):
            try:
                while left:
                    block = handle.read(min(65536, left))
                    if not block:
                        break
                    left -= len(block)
                    yield block
            finally:
                handle.close()

        response = StreamingResponse(
            chunks(source, size), status_code=200, media_type=media, headers=headers
        )
        source = None
        return response
    except (OSError, FileNotFoundError) as exc:
        raise HTTPException(status_code=404, detail="文件或任务不存在") from exc
    finally:
        if source is not None:
            source.close()


def _published_artifact(relative: str, request: Request, *, head: bool):
    service = _service(request)
    try:
        path = artifact_path(service.project, relative)
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=404, detail="文件或任务不存在") from exc
    return _artifact_response(path, head=head)


@router.get("/artifacts/{relative:path}", operation_id="read_management_artifact")
def read_management_artifact(relative: str, request: Request, _: CurrentUser):
    return _published_artifact(relative, request, head=False)


@router.head("/artifacts/{relative:path}", operation_id="head_management_artifact")
def head_management_artifact(relative: str, request: Request, _: CurrentUser):
    return _published_artifact(relative, request, head=True)
