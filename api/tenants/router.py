"""Tenant and empty-template routes. Existing account and data routes stay in place."""

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from ..auth.dependencies import AdminUser

router = APIRouter(prefix="/tenants", tags=["tenants"])


class TemplateCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    purpose: str = Field(pattern=r"^(production|development)$")
    name: str = Field(min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=500)


class TenantCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    template_id: int
    name: str = Field(min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=500)


class TenantAccountCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    account_name: str = Field(min_length=1, max_length=100)
    exchange: str | None = Field(default=None, pattern=r"^binance$")
    environment: str | None = Field(default=None, pattern=r"^(production|sandbox)$")
    api_key: str | None = Field(default=None, max_length=2048, repr=False)
    secret: str | None = Field(default=None, max_length=2048, repr=False)
    passphrase: str | None = Field(default=None, max_length=2048, repr=False)
    initial_capital: str | None = Field(default=None, max_length=40)
    quote_currency: str | None = Field(default=None, pattern=r"^[A-Z]{3,8}$")


class DataPolicyBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: str = Field(min_length=1, max_length=30)
    product: str = "futures_um"
    base_level: str = "tick"
    intervals: list[str] = Field(default_factory=list)
    realtime_enabled: bool = False
    history_enabled: bool = False
    history_mode: str = "start"
    history_start: str | None = None
    history_days: int = 30
    underlying: str | None = None


def tenants(request: Request):
    service = getattr(request.app.state, "tenants", None)
    if service is None:
        raise HTTPException(503, "租户服务不可用。")
    return service


@router.get("/templates")
def list_templates(request: Request, _: AdminUser):
    return tenants(request).list_templates()


@router.post("/templates", status_code=201)
def create_template(request: Request, payload: TemplateCreate, _: AdminUser):
    return tenants(request).create_template(payload.purpose, payload.name, payload.description)


@router.get("")
def list_tenants(request: Request, _: AdminUser):
    return tenants(request).list_tenants()


@router.post("", status_code=201)
def create_tenant(request: Request, payload: TenantCreate, _: AdminUser):
    return tenants(request).create_tenant(payload.template_id, payload.name, payload.description)


@router.post("/{tenant_id}/archive")
def archive_tenant(request: Request, tenant_id: int, _: AdminUser):
    return tenants(request).archive(tenant_id)


@router.post("/{tenant_id}/restore")
def restore_tenant(request: Request, tenant_id: int, _: AdminUser):
    return tenants(request).restore(tenant_id)


@router.delete("/{tenant_id}", status_code=204)
def delete_tenant(request: Request, tenant_id: int, _: AdminUser):
    tenants(request).delete(tenant_id)
    return Response(status_code=204)


@router.get("/{tenant_id}/accounts")
def list_accounts(request: Request, tenant_id: int, _: AdminUser):
    return tenants(request).list_accounts(tenant_id)


@router.post("/{tenant_id}/accounts", status_code=201)
def add_account(request: Request, tenant_id: int, payload: TenantAccountCreate, _: AdminUser):
    return tenants(request).add_account(tenant_id, payload)


@router.get("/{tenant_id}/subscriptions")
def list_subscriptions(request: Request, tenant_id: int, _: AdminUser):
    return tenants(request).list_subscriptions(tenant_id)


@router.post("/{tenant_id}/subscriptions")
def save_subscription(request: Request, tenant_id: int, payload: DataPolicyBody, _: AdminUser):
    return tenants(request).save_subscription(tenant_id, payload)
