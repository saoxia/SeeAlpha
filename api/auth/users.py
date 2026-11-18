"""Admin user directory. Tenants stay on the existing user table."""

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from .dependencies import AdminUser, CurrentContext, actor_is_admin
from .manage import set_user_password

router = APIRouter(prefix="/users", tags=["users"])


class UserCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256, repr=False)


class ManagedUser(BaseModel):
    id: int
    username: str
    created_at: int
    production_tenant_id: int
    production_tenant_name: str


def _require_console(context: CurrentContext) -> None:
    if context.impersonating:
        raise HTTPException(status_code=409, detail="请先退出当前用户视图。")


def _published(request: Request, user) -> ManagedUser:
    tenant = request.app.state.tenants.repository.production_for_owner(user["id"])
    if tenant is None:
        raise HTTPException(status_code=503, detail="用户缺少正式租户。")
    return ManagedUser(
        id=user["id"],
        username=user["username"],
        created_at=user["created_at"],
        production_tenant_id=tenant["id"],
        production_tenant_name=tenant["name"],
    )


@router.get("", response_model=list[ManagedUser])
def list_users(request: Request, context: CurrentContext, _: AdminUser) -> list[ManagedUser]:
    _require_console(context)
    return [_published(request, user) for user in request.app.state.repository.list_users()
            if not actor_is_admin(request, user["username"])]


@router.post("", response_model=ManagedUser, status_code=201)
def create_user(
    payload: UserCreate, request: Request, context: CurrentContext, _: AdminUser,
) -> ManagedUser:
    _require_console(context)
    if actor_is_admin(request, payload.username):
        raise HTTPException(status_code=422, detail="管理员名称不可用于普通用户账户。")
    try:
        set_user_password(request.app.state.database, payload.username, payload.password,
                          create=True, admin_username=request.app.state.settings.admin_username)
    except ValueError as exc:
        status = 409 if str(exc) == "用户已存在。" else 422
        raise HTTPException(status_code=status, detail=str(exc)) from exc
    user = request.app.state.repository.get_user_by_username(payload.username)
    # The background tenant reconciler registers the new Engine tenant.
    # User creation is a business-database operation even during Engine outages.
    return _published(request, user)


class LegacyTenantTransfer(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    target_username: str = Field(min_length=3, max_length=64)
    confirm: bool = False


def _legacy_admin_tenants(request: Request):
    admin = request.app.state.repository.get_user_by_username(request.app.state.settings.admin_username)
    admin_id = admin["id"] if admin else None
    tenants = request.app.state.tenants.repository
    rows = []
    for tenant in tenants.list_tenants():
        owned_by_admin = admin_id is not None and tenant["owner_id"] == admin_id
        unclaimed_legacy = tenant["owner_id"] is None and tenant["system_key"] == "default"
        if not (owned_by_admin or unclaimed_legacy):
            continue
        children = tenants.count_children(tenant["id"])
        # The fresh, empty system seed is not a historical trading tenant.
        # Unowned default policies alone are platform market-data configuration,
        # not evidence that the tenant belonged to a former trading user.
        if unclaimed_legacy and not (children["exchange_accounts"] or children["virtual_accounts"]):
            continue
        rows.append({
            "id": tenant["id"], "name": tenant["name"],
            "purpose": tenant["purpose"], "status": tenant["status"],
            "owner_id": tenant["owner_id"], **children,
        })
    return admin_id, rows


@router.get("/legacy-tenants")
def legacy_tenants(request: Request, _: AdminUser):
    """Read-only inventory; migration requires an explicit target and confirmation."""
    _admin_id, rows = _legacy_admin_tenants(request)
    return rows


@router.post("/legacy-tenants/{tenant_id}/transfer")
def transfer_legacy_tenant(tenant_id: int, payload: LegacyTenantTransfer,
                           request: Request, _: AdminUser):
    if not payload.confirm:
        raise HTTPException(status_code=422, detail="必须明确确认迁移旧租户的数据归属。")
    if actor_is_admin(request, payload.target_username):
        raise HTTPException(status_code=422, detail="目标必须是普通用户账户。")
    target = request.app.state.repository.get_user_by_username(payload.target_username)
    if target is None:
        raise HTTPException(status_code=404, detail="目标普通用户不存在。")
    admin_id, rows = _legacy_admin_tenants(request)
    if admin_id is None or not any(row["id"] == tenant_id for row in rows):
        raise HTTPException(status_code=404, detail="可迁移的旧管理员租户不存在。")
    try:
        tenant = request.app.state.tenants.repository.transfer_legacy_owner(
            tenant_id, admin_id, target["id"],
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"id": tenant["id"], "name": tenant["name"],
            "owner_id": tenant["owner_id"], "target_username": target["username"]}
