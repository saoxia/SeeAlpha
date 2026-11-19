"""User-scoped controls for the development Engine paired with AI Harness.

Platform selects the Engine Docker and owns these operations. No Engine bearer,
filesystem path, Docker command or reset primitive is accepted from the browser.
"""
from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from core.engine_contracts import EngineConflict

from ..auth.dependencies import CurrentUser
from ..market_data.contracts import DataJobRequest
from ..tenant_docker import DockerTenantManager
from .harness_router import _client, _require_harness_owner
from .task_access import allowed_scope

router = APIRouter(prefix="/harness/development-engine", tags=["research-harness"])
_TERMINAL_DATA = {"succeeded", "failed", "cancelled", "interrupted"}
_TERMINAL_TASK = {"SUCCEEDED", "FAILED", "CANCELLED", "INTERRUPTED"}
_TERMINAL_HARNESS = {"completed", "failed", "cancelled", "interrupted"}


class DatasetSelection(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    tenant_id: int = Field(gt=0)
    account_id: str = Field(min_length=1, max_length=40)
    dataset_ref: str = Field(min_length=1, max_length=128)


class PrepareRequest(DatasetSelection):
    start: datetime
    end: datetime


class ResetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    tenant_id: int = Field(gt=0)
    confirmation: str = Field(max_length=80)


def _development_engine(request: Request, user: CurrentUser, tenant_id: int):
    _require_harness_owner(request, user)
    if not request.app.state.settings.harness_enabled:
        raise HTTPException(503, "AI Harness 尚未启用。")
    tenant = request.app.state.tenants.require_owned_active(tenant_id, user.id)
    if tenant["purpose"] != "development":
        raise HTTPException(403, "仅允许操作本人开发租户的 Engine。")
    deployment = request.app.state.production_deployments
    manager = deployment.manager if deployment is not None else None
    if not isinstance(manager, DockerTenantManager):
        raise HTTPException(503, "开发 Engine 管理仅支持本机独立 Docker 部署。")
    try:
        manager.endpoint(tenant_id)
    except (OSError, ValueError, EngineConflict):
        raise HTTPException(503, "开发 Engine 尚未部署或不可用。") from None
    return manager


def _dataset(request: Request, user: CurrentUser, selection: DatasetSelection):
    _development_engine(request, user, selection.tenant_id)
    if not allowed_scope(
        request.app, user.id, selection.tenant_id, selection.account_id,
        selection.dataset_ref, require_issued=False,
    ):
        raise HTTPException(403, "数据集须属于本人开发租户、虚拟账户及已授权历史订阅。")
    product, symbol, interval = selection.dataset_ref.split(":")
    return product, symbol, interval


def _prepare_payload(body: PrepareRequest, dataset: tuple[str, str, str]):
    if (body.start.tzinfo is None or body.end.tzinfo is None
            or body.start >= body.end or body.end - body.start > timedelta(days=31)
            or body.end > datetime.now(UTC)):
        raise HTTPException(422, "请选择不超过 31 天、已结束且带时区的 UTC 历史区间。")
    product, symbol, interval = dataset
    return DataJobRequest(
        action="prepare", product=product, symbol=symbol,
        intervals=[interval], start=body.start, end=body.end,
    ).model_dump(mode="json", exclude_none=True)


@router.post("/data/plan")
def prepare_plan(request: Request, body: PrepareRequest, user: CurrentUser):
    dataset = _dataset(request, user, body)
    payload = _prepare_payload(body, dataset)
    return request.app.state.engine.data_query(
        "prepare-plan", payload, tenant_id=body.tenant_id,
    )


@router.post("/data/prepare", status_code=202)
def prepare_data(request: Request, body: PrepareRequest, user: CurrentUser):
    dataset = _dataset(request, user, body)
    payload = _prepare_payload(body, dataset)
    return request.app.state.engine.market_control(
        "job/start", {"payload": payload}, tenant_id=body.tenant_id,
    )


def _jobs_for_dataset(request: Request, tenant_id: int, dataset):
    product, symbol, interval = dataset
    jobs = request.app.state.engine.market_control("jobs", tenant_id=tenant_id)
    return [
        job for job in jobs
        if job.get("action") == "prepare"
        and job.get("parameters", {}).get("product", "futures_um") == product
        and job.get("parameters", {}).get("symbol") == symbol
        and interval in job.get("parameters", {}).get("intervals", [])
    ]


@router.get("/data/jobs")
def list_data_jobs(request: Request, tenant_id: int, account_id: str,
                   dataset_ref: str, user: CurrentUser):
    selection = DatasetSelection(
        tenant_id=tenant_id, account_id=account_id, dataset_ref=dataset_ref,
    )
    dataset = _dataset(request, user, selection)
    return {"jobs": _jobs_for_dataset(request, tenant_id, dataset)}


@router.post("/data/jobs/{identity}/cancel")
def cancel_data_job(request: Request, identity: str, body: DatasetSelection,
                    user: CurrentUser):
    dataset = _dataset(request, user, body)
    if not re.fullmatch(r"[0-9a-f]{32}", identity):
        raise HTTPException(404, "历史数据任务不存在。")
    if not any(job.get("id") == identity
               for job in _jobs_for_dataset(request, body.tenant_id, dataset)):
        raise HTTPException(404, "历史数据任务不存在。")
    return request.app.state.engine.market_control(
        "job/cancel", {"id": identity}, tenant_id=body.tenant_id,
    )


@router.post("/reset")
def reset_development_engine(request: Request, body: ResetRequest, user: CurrentUser):
    manager = _development_engine(request, user, body.tenant_id)
    if body.confirmation != f"RESET ENGINE {body.tenant_id}":
        raise HTTPException(422, "请完整输入指定的 Engine 重置确认文本。")

    # No implicit task cancellation or background reset. Unknown task states deny reset.
    data_jobs = request.app.state.engine.market_control(
        "jobs", tenant_id=body.tenant_id,
    )
    if any(job.get("status") not in _TERMINAL_DATA for job in data_jobs):
        raise HTTPException(409, "开发 Engine 仍有未结束的历史数据任务。")
    tasks = request.app.state.engine.list_tasks(body.tenant_id)
    if any(task.get("status") not in _TERMINAL_TASK for task in tasks):
        raise HTTPException(409, "开发 Engine 仍有未结束的回测或检查任务。")
    with _client(request, user) as client:
        if any(item.get("status") not in _TERMINAL_HARNESS
               for item in client.recent_tasks()):
            raise HTTPException(409, "AI Harness 仍有未结束的研究任务。")

    backup = manager.reset_development(body.tenant_id)
    request.app.state.engine.ensure_tenant(body.tenant_id, "development")
    return {
        "tenant_id": body.tenant_id, "status": "ready",
        "backup": backup, "historical_data_preserved": True,
    }
