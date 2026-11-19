"""Admin user directory. Tenants stay on the existing user table."""

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from .dependencies import AdminUser, actor_is_admin
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
def list_users(request: Request, _: AdminUser) -> list[ManagedUser]:
    return [_published(request, user) for user in request.app.state.repository.list_users()
            if not actor_is_admin(request, user["username"])]


@router.post("", response_model=ManagedUser, status_code=201)
def create_user(
    payload: UserCreate, request: Request, _: AdminUser,
) -> ManagedUser:
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

