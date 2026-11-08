"""Admin user directory. Tenants stay on the existing user table."""

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from .dependencies import AdminUser, CurrentContext
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
    return [_published(request, user) for user in request.app.state.repository.list_users()]


@router.post("", response_model=ManagedUser, status_code=201)
def create_user(
    payload: UserCreate, request: Request, context: CurrentContext, _: AdminUser,
) -> ManagedUser:
    _require_console(context)
    try:
        set_user_password(request.app.state.database, payload.username, payload.password, create=True)
    except ValueError as exc:
        status = 409 if str(exc) == "用户已存在。" else 422
        raise HTTPException(status_code=status, detail=str(exc)) from exc
    user = request.app.state.repository.get_user_by_username(payload.username)
    tenant = request.app.state.tenants.repository.production_for_owner(user["id"])
    request.app.state.engine.ensure_tenant(tenant["id"], "production")
    return _published(request, user)
