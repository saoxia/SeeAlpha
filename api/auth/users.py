"""Admin user directory. Tenants stay on the existing user table."""

from fastapi import APIRouter, HTTPException, Request

from core.engine_contracts import EngineConflict, QueryError
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
    engine_node: str | None


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
        engine_node=(request.app.state.production_deployments.placement(tenant["id"])
                     if request.app.state.production_deployments is not None else None),
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
    # User creation only creates business records. Production Engine placement
    # and provisioning require a separate administrator operation.
    return _published(request, user)



class EnginePlacement(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    node_id: str = Field(min_length=1, max_length=64)


@router.get("/engine-nodes")
def engine_nodes(request: Request, _: AdminUser):
    manager = request.app.state.production_deployments
    return {
        "deployment": request.app.state.settings.engine_deployment,
        "nodes": manager.choices() if manager is not None else [],
    }


@router.post("/{user_id}/engine", response_model=ManagedUser)
def deploy_user_engine(
    user_id: int, payload: EnginePlacement, request: Request, _: AdminUser,
) -> ManagedUser:
    manager = request.app.state.production_deployments
    if manager is None:
        raise HTTPException(409, "当前是 single 模式，无法创建独立 Docker。")
    user = request.app.state.repository.get_user_by_id(user_id)
    if user is None or actor_is_admin(request, user["username"]):
        raise HTTPException(404, "普通用户不存在。")
    tenant = request.app.state.tenants.repository.production_for_owner(user_id)
    if tenant is None or tenant["status"] != "active":
        raise HTTPException(409, "用户正式租户不可部署。")
    try:
        manager.assign(tenant["id"], payload.node_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    try:
        request.app.state.engine.ensure_tenant(tenant["id"], "production")
    except (OSError, QueryError, EngineConflict) as exc:
        raise HTTPException(503, "节点已选定，但 Engine 尚未部署成功；后台将重试。") from exc
    return _published(request, user)
