"""Tenant and empty-template routes. Existing account and data routes stay in place."""

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from ..auth.dependencies import AdminUser, CurrentContext, CurrentUser, actor_is_admin

router = APIRouter(prefix="/tenants", tags=["tenants"])


class TemplateCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    purpose: str = Field(pattern=r"^(production|development)$")
    name: str = Field(min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=500)


class TenantCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=500)
    purpose: str | None = None
    template_id: int | None = None


class TenantCopy(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=500)


class TenantShare(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=100)
    username: str = Field(min_length=1, max_length=64)
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
def list_tenants(request: Request, user: CurrentUser):
    return tenants(request).list_tenants(user.id)


@router.post("", status_code=201)
def create_tenant(
    request: Request, payload: TenantCreate, user: CurrentUser, context: CurrentContext,
):
    if payload.purpose not in (None, "development", "production"):
        raise HTTPException(422, "租户类型只能是正式或开发。")
    purpose = payload.purpose or "development"
    if payload.template_id is not None:
        template = tenants(request).repository.get_template(payload.template_id)
        if template is None:
            raise HTTPException(404, "模板不存在。")
        if payload.purpose is not None and payload.purpose != template["purpose"]:
            raise HTTPException(422, "模板类型与租户用途不匹配。")
        purpose = template["purpose"]
    if purpose == "production" and not actor_is_admin(request, context.actor.username):
        raise HTTPException(403, "仅管理员可创建额外的正式租户。")
    return tenants(request).create_for_owner(user.id, purpose, payload.name, payload.description)


@router.post("/{tenant_id}/copy", status_code=201)
def copy_tenant(request: Request, tenant_id: int, payload: TenantCopy, user: CurrentUser):
    return tenants(request).copy_for_owner(
        tenant_id, user.id, payload.name, payload.description
    )


@router.post("/{tenant_id}/share", status_code=201)
def share_tenant(request: Request, tenant_id: int, payload: TenantShare, user: CurrentUser):
    return tenants(request).share_with_user(
        tenant_id, user.id, payload.username, payload.name, payload.description
    )


@router.post("/{tenant_id}/archive")
def archive_tenant(request: Request, tenant_id: int, user: CurrentUser):
    return tenants(request).archive(tenant_id, user.id)


@router.post("/{tenant_id}/restore")
def restore_tenant(request: Request, tenant_id: int, user: CurrentUser):
    return tenants(request).restore(tenant_id, user.id)


@router.delete("/{tenant_id}", status_code=204)
def delete_tenant(request: Request, tenant_id: int, user: CurrentUser):
    tenants(request).delete(tenant_id, user.id)
    return Response(status_code=204)


@router.get("/{tenant_id}/accounts")
def list_accounts(request: Request, tenant_id: int, user: CurrentUser):
    return tenants(request).list_accounts(tenant_id, user.id)


@router.post("/{tenant_id}/accounts", status_code=201)
def add_account(request: Request, tenant_id: int, payload: TenantAccountCreate, user: CurrentUser):
    return tenants(request).add_account(tenant_id, payload, user.id)


@router.get("/{tenant_id}/subscriptions")
def list_subscriptions(request: Request, tenant_id: int, user: CurrentUser):
    return tenants(request).list_subscriptions(tenant_id, user.id)


@router.post("/{tenant_id}/subscriptions")
def save_subscription(request: Request, tenant_id: int, payload: DataPolicyBody, user: CurrentUser):
    return tenants(request).save_subscription(tenant_id, payload, user.id)
