"""Admin user directory. Tenants stay on the existing user table."""

from decimal import Decimal
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

from core.engine_contracts import EngineConflict, QueryError

from ..config import PROJECT
from .dependencies import AdminUser, actor_is_admin
from .manage import set_user_password

router = APIRouter(prefix="/users", tags=["users"])


class ResourceAllocation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = True
    cpus: str | None = Field(default=None, pattern=r"^(?:0\.[1-9]|[1-9][0-9]?(?:\.[0-9])?)$")
    memory: str | None = Field(default=None, pattern=r"^[1-9][0-9]*[mg]$")
    node: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_-]{0,63}$")

    @field_validator("cpus")
    @classmethod
    def cpu_limit(cls, value):
        if value is not None and Decimal(value) > 64:
            raise ValueError("CPU 配额不得超过 64 核。")
        return value

    @field_validator("memory")
    @classmethod
    def memory_limit(cls, value):
        if value is not None:
            amount, unit = int(value[:-1]), value[-1]
            if amount * (1024 if unit == "g" else 1) > 256 * 1024:
                raise ValueError("内存配额不得超过 256 GiB。")
        return value


class DevelopmentAllocation(ResourceAllocation):
    realtime_enabled: bool = False


class UserResourcesBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    harness: ResourceAllocation
    development: DevelopmentAllocation
    production: ResourceAllocation



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
    resources: UserResourcesBody


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
        resources=UserResourcesBody.model_validate(
            request.app.state.user_resources.get(user["id"])
        ),
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


@router.get("/resource-capabilities")
def resource_capabilities(request: Request, _: AdminUser):
    settings = request.app.state.settings
    hub = request.app.state.node_hub
    nodes = [{"id": "local", "online": True}]
    if hub is not None:
        nodes += hub.describe()["nodes"]
    return {
        "nodes": nodes,
        "defaults": {
            "harness": {"cpus": settings.harness_cpus, "memory": settings.harness_memory},
            "development": {
                "cpus": settings.engine_development_cpus,
                "memory": settings.engine_development_memory,
            },
            "production": {
                "cpus": settings.engine_production_cpus,
                "memory": settings.engine_production_memory,
            },
        },
        "harness_deployment": settings.harness_deployment if settings.harness_enabled else "disabled",
        "engine_deployment": settings.engine_deployment,
    }


@router.put("/{user_id}/resources", response_model=ManagedUser)
def update_user_resources(
    user_id: int, body: UserResourcesBody, request: Request, _: AdminUser,
) -> ManagedUser:
    user = request.app.state.repository.get_user_by_id(user_id)
    if user is None or actor_is_admin(request, user["username"]):
        raise HTTPException(404, "普通用户不存在。")
    service = request.app.state.user_resources
    before = service.get(user_id)
    proposed = body.model_dump()
    manager = request.app.state.production_deployments
    tenant = request.app.state.tenants.repository.production_for_owner(user_id)
    if (manager is not None and tenant is not None
            and manager.placement(tenant["id"])
            and (not proposed["production"]["enabled"]
                 or any(proposed["production"][key] != before["production"][key]
                        for key in ("cpus", "memory")))):
        raise HTTPException(409, "已部署的正式 Docker 不可在此直接停用或调整配额，须走受控停机/升级流程。")
    developments = [
        tenant for tenant in request.app.state.tenants.repository.list_tenants(user_id)
        if tenant["purpose"] == "development"
    ]
    if developments and any(
        proposed["development"][key] != before["development"][key]
        for key in ("cpus", "memory", "node")
    ):
        raise HTTPException(409, "已有开发租户，请先通过受控迁移流程调整其资源参数。")
    harness_root = Path(request.app.state.settings.harness_root or PROJECT / ".local/harness-users")
    if (harness_root / "credentials" / f"user-{user_id}.token").exists() and any(
        proposed["harness"][key] != before["harness"][key]
        for key in ("cpus", "memory", "node")
    ):
        raise HTTPException(409, "已有 Harness 实例，请先通过受控迁移流程调整其资源参数。")
    nodes = {"local"}
    hub = request.app.state.node_hub
    if hub is not None:
        nodes.update(node["id"] for node in hub.describe()["nodes"] if node["online"])
    for kind in ("harness", "development"):
        target = proposed[kind]["node"]
        if target is None or target == "local":
            continue
        if target not in nodes:
            raise HTTPException(422, "资源节点未注册或不在线。")
        if kind == "harness" and request.app.state.settings.harness_deployment != "per_user":
            raise HTTPException(409, "只有每用户 Harness 模式支持节点分配。")
        if kind == "development" and request.app.state.settings.engine_deployment != "distributed":
            raise HTTPException(409, "只有分布式 Engine 模式支持远端开发节点。")
    if proposed["production"]["node"] is not None:
        raise HTTPException(422, "正式 Docker 节点请通过独立部署操作指定。")
    service.save(user_id, proposed)
    return _published(request, user)


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
    if not request.app.state.user_resources.enabled(user_id, "production"):
        raise HTTPException(403, "此用户未分配正式 Docker 资源。")
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
