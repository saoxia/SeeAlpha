"""Catalog, tenant-type grants, and issuance of strategy packages."""

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from ..auth.dependencies import AdminUser

router = APIRouter(tags=["strategy-store"])


class PackageCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    code_key: str = Field(pattern=r"^[a-z][a-z0-9_]{1,40}$")
    name: str = Field(min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=500)


class VersionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    version: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,39}$")
    parameters: dict = Field(default_factory=dict)


class GrantCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    purpose: str = Field(pattern=r"^(production|development)$")


class LoadCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    account_id: int | str
    version_id: int
    parameters: dict | None = None


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
    return store(request).create_package(payload.code_key, payload.name, payload.description)


@router.post("/strategy-packages/{package_id}/versions", status_code=201)
def create_version(request: Request, package_id: int, payload: VersionCreate, _: AdminUser):
    return store(request).create_version(package_id, payload.version, payload.parameters)


@router.post("/strategy-packages/versions/{version_id}/grants", status_code=201)
def grant_version(request: Request, version_id: int, payload: GrantCreate, _: AdminUser):
    return store(request).grant(version_id, payload.purpose)


@router.get("/tenants/{tenant_id}/strategy-loads")
def list_loads(request: Request, tenant_id: int, _: AdminUser):
    return store(request).list_loads(tenant_id)


@router.post("/tenants/{tenant_id}/strategy-loads", status_code=201)
def issue_load(request: Request, tenant_id: int, payload: LoadCreate, _: AdminUser):
    return store(request).issue(tenant_id, payload.account_id, payload.version_id, payload.parameters)
