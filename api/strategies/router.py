"""Catalog, tenant-type grants, and issuance of strategy packages."""

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from ..auth.dependencies import AdminUser

router = APIRouter(tags=["strategy-store"])


class PackageCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    archive: str = Field(min_length=1, max_length=400_000)
    parameters: dict = Field(default_factory=dict)


class VersionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    archive: str = Field(min_length=1, max_length=400_000)
    parameters: dict = Field(default_factory=dict)


class GrantCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    purpose: str = Field(pattern=r"^(production|development)$")


class LoadCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    account_id: int | str
    version_id: int
    parameters: dict | None = None


class BacktestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start: str = Field(min_length=10, max_length=40)
    end: str = Field(min_length=10, max_length=40)
    interval: str = Field(default="1m", pattern=r"^(1m|5m)$")


class AsyncBacktestRequest(BacktestRequest):
    idempotency_key: str | None = Field(
        default=None, min_length=1, max_length=128,
        pattern=r"^[A-Za-z0-9._:-]{1,128}$",
    )


def store(request: Request):
    service = getattr(request.app.state, "strategies", None)
    if service is None:
        raise HTTPException(503, "策略商店不可用。")
    return service


@router.get("/strategy-packages")
def list_packages(request: Request, _: AdminUser):
    return store(request).list_packages()


@router.post("/strategy-packages", status_code=201)
def create_package(request: Request, payload: PackageCreate, _: AdminUser):
    return store(request).create_package(payload.archive, payload.parameters)


@router.post("/strategy-packages/{package_id}/versions", status_code=201)
def create_version(request: Request, package_id: int, payload: VersionCreate, _: AdminUser):
    return store(request).create_version(package_id, payload.archive, payload.parameters)


@router.post("/strategy-packages/versions/{version_id}/grants", status_code=201)
def grant_version(request: Request, version_id: int, payload: GrantCreate, _: AdminUser):
    return store(request).grant(version_id, payload.purpose)


@router.get("/tenants/{tenant_id}/strategy-loads")
def list_loads(request: Request, tenant_id: int, _: AdminUser):
    return store(request).list_loads(tenant_id)


@router.post("/tenants/{tenant_id}/strategy-loads", status_code=201)
def issue_load(request: Request, tenant_id: int, payload: LoadCreate, _: AdminUser):
    return store(request).issue(tenant_id, payload.account_id, payload.version_id, payload.parameters)


@router.post("/tenants/{tenant_id}/accounts/{account_id}/backtests")
def start_backtest(
    request: Request, tenant_id: int, account_id: int, payload: BacktestRequest, _: AdminUser,
):
    return store(request).backtest(tenant_id, account_id, payload.start, payload.end, payload.interval)


@router.get("/tenants/{tenant_id}/accounts/{account_id}/backtests/latest")
def latest_backtest(request: Request, tenant_id: int, account_id: int, _: AdminUser):
    return store(request).latest_backtest(tenant_id, account_id)


@router.post("/tenants/{tenant_id}/accounts/{account_id}/backtests/async", status_code=202)
def submit_backtest_async(
    request: Request, tenant_id: int, account_id: int,
    payload: AsyncBacktestRequest, _: AdminUser,
):
    return store(request).submit_backtest(
        tenant_id, account_id, payload.start, payload.end, payload.interval,
        idempotency_key=payload.idempotency_key,
    )


@router.get("/tenants/{tenant_id}/jobs")
def list_tenant_jobs(request: Request, tenant_id: int, _: AdminUser):
    return {"items": store(request).tasks(tenant_id)}


@router.get("/tenants/{tenant_id}/jobs/{identity}")
def get_tenant_job(request: Request, tenant_id: int, identity: str, _: AdminUser):
    return store(request).task(tenant_id, identity)


@router.get("/tenants/{tenant_id}/jobs/{identity}/result")
def tenant_job_result(request: Request, tenant_id: int, identity: str, _: AdminUser):
    return store(request).task_result(tenant_id, identity)


@router.post("/tenants/{tenant_id}/jobs/{identity}/cancel")
def cancel_tenant_job(request: Request, tenant_id: int, identity: str, _: AdminUser):
    return store(request).cancel_task(tenant_id, identity)


@router.post("/tenants/{tenant_id}/jobs/{identity}/retry", status_code=202)
def retry_tenant_job(request: Request, tenant_id: int, identity: str, _: AdminUser):
    return store(request).retry_task(tenant_id, identity)
