"""Independent API entry point. Research jobs start here; no legacy console is imported."""

import asyncio
import logging
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from starlette.responses import JSONResponse

from core.engine_contracts import EngineConflict, QueryError

from .auth.router import router
from .auth.users import router as users_router
from .config import PROJECT, Settings
from .db.auth_repository import auth_repository
from .db.database import create_database
from .db.errors import StorageUnavailable
from .distributed_docker import DistributedTenantManager
from .engine_client import EngineClient, EngineUnavailable
from .equity.router import router as equity_router
from .equity.service import EquityService
from .exchanges.accounts import Accounts
from .exchanges.router import router as exchanges_router
from .exchanges.service import ExchangeService
from .exchanges.trading import ManualTrading
from .exchanges.trading import router as trading_router
from .management.router import router as management_router
from .management.service import management_runtime
from .market_data.router import router as market_data_router
from .market_data.service import DataService
from .node.hub import NodeHub
from .node_routes import router as node_router
from .olap.repository import olap_repository
from .olap.router import router as olap_router
from .olap.service import ClickhouseMonitor
from .operations.router import router as operations_router
from .operations.service import OperationService
from .production_deployments import ProductionDeploymentManager
from .research.harness_instances import UserHarnessInstances
from .research.harness_router import router as harness_router
from .research.task_access import ResearchLeases
from .research.task_router import router as research_task_router
from .strategies.router import router as strategies_router
from .strategies.service import StrategyStore
from .tenant_docker import DockerTenantManager
from .tenants.router import router as tenants_router
from .tenants.service import TenantService
from .user_resources import UserResources

_TENANT_RECONCILE_SECONDS = 15


def create_app(settings: Settings | None = None, *, project: Path | None = None) -> FastAPI:
    settings = settings or Settings.from_environment()
    if settings.require_database_url and not settings.engine_url.startswith(("http://", "https://")):
        raise ValueError("业务 API 不允许进程内 Engine；请配置 ENGINE_URL。")
    database = create_database(settings)

    @asynccontextmanager
    async def lifespan(app):
        task = None
        tenant_task = None
        olap_task = None
        configuration_task = None
        portfolio_task = None
        runtime = None
        try:
            database.initialize()
            if settings.seed_demo_strategies:
                await asyncio.to_thread(app.state.strategies.seed_system_demos)
            if manager is not None:
                default = app.state.tenants.repository.require_default()
                if default is not None:
                    manager.bind_market_tenant(default["id"])
            async def reconcile_tenants():
                # Engine registration is not a prerequisite for auth or business APIs.
                # Retry from the database so tenants created during an outage are
                # eventually registered without restarting Platform.
                while True:
                    try:
                        tenants = await asyncio.to_thread(
                            app.state.tenants.repository.list_tenants
                        )
                    except (StorageUnavailable, OSError) as exc:
                        logging.getLogger(__name__).warning(
                            "租户列表读取失败，稍后重试：%s", exc,
                        )
                    else:
                        for tenant in tenants:
                            if (tenant["owner_id"] is not None
                                    and not app.state.user_resources.enabled(
                                        tenant["owner_id"], tenant["purpose"])):
                                continue
                            if tenant["status"] != "active" or (
                                app.state.production_deployments is not None
                                and not app.state.production_deployments.eligible(
                                    tenant["id"], tenant["purpose"])
                            ):
                                continue
                            try:
                                await asyncio.to_thread(
                                    app.state.engine.ensure_tenant,
                                    tenant["id"], tenant["purpose"], tenant["status"],
                                )
                            except (OSError, QueryError, EngineConflict) as exc:
                                logging.getLogger(__name__).warning(
                                    "租户 %s 的 Engine 注册未完成，稍后重试：%s",
                                    tenant["id"], exc,
                                )
                    await asyncio.sleep(_TENANT_RECONCILE_SECONDS)

            tenant_task = asyncio.create_task(reconcile_tenants())
            if app.state.management_project is not None:
                # Another process may already hold the management lock. Startup
                # still continues; only that holder runs and recovers jobs.
                runtime = management_runtime(app.state.management_project, database)
                app.state.management = runtime.__enter__()
            app.state.operations.repository.initialize()
            app.state.olap.repository.initialize()
            async def settle_market_jobs():
                try:
                    await asyncio.to_thread(app.state.market_data.reconcile_jobs)
                except (OSError, HTTPException, StorageUnavailable, QueryError):
                    logging.getLogger(__name__).warning(
                        "行情任务的业务配置结算未完成，保留记录以便下次重试。",
                    )

            await settle_market_jobs()
            await asyncio.to_thread(app.state.market_data.subscriptions.restore)
            await app.state.market_data.subscriptions.sync_options(app.state.exchanges)

            async def reconcile_configuration():
                while True:
                    await asyncio.sleep(15)
                    await settle_market_jobs()
                    await asyncio.to_thread(app.state.market_data.subscriptions.restore)
                    await app.state.market_data.subscriptions.sync_options(app.state.exchanges)

            configuration_task = asyncio.create_task(reconcile_configuration())
            portfolio_task = asyncio.create_task(app.state.exchanges.portfolio_worker())

            async def refresh_olap():
                while True:
                    await asyncio.to_thread(app.state.olap.refresh_freshness_cache)
                    await asyncio.sleep(60)

            olap_task = asyncio.create_task(refresh_olap())
            if settings.operation_sync_seconds:
                task = asyncio.create_task(
                    app.state.operations.worker(settings.operation_sync_seconds)
                )
            if settings.equity_enabled and database.backend == "orm":
                await app.state.equity.start(settings.database_path.parent / "equity.lock")
            yield
        finally:
            await app.state.equity.close()
            for background in (tenant_task, configuration_task, portfolio_task):
                if background is not None:
                    background.cancel()
                    with suppress(asyncio.CancelledError):
                        await background
            await asyncio.to_thread(app.state.market_data.close)
            if olap_task is not None:
                olap_task.cancel()
                with suppress(asyncio.CancelledError):
                    await olap_task
            if task is not None:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
            if app.state.management is not None and runtime is not None:
                runtime.__exit__(None, None, None)
                app.state.management = None
            app.state.engine.close()
            database.close()

    app = FastAPI(title="SeeAlpha API", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.repository = auth_repository(database, admin_username=settings.admin_username)
    app.state.database = database
    app.state.user_resources = UserResources(database)
    app.state.research_leases = (
        ResearchLeases(settings.database_path.parent / "research-tasks" / "leases.sqlite3")
        if settings.research_proxy_enabled else None
    )
    app.state.accounts = Accounts(database, settings)
    if settings.engine_deployment not in {"single", "docker", "distributed"}:
        raise ValueError("ENGINE_DEPLOYMENT must be single, docker or distributed")
    def tenant_limits(tenant_id, purpose):
        allocated = app.state.user_resources.for_tenant(tenant_id, purpose)
        defaults = {
            "production": (settings.engine_production_cpus, settings.engine_production_memory),
            "development": (settings.engine_development_cpus, settings.engine_development_memory),
        }
        cpu, memory = defaults[purpose]
        return allocated["cpus"] or cpu, allocated["memory"] or memory

    def development_node(tenant_id):
        return app.state.user_resources.for_tenant(tenant_id, "development")["node"]

    manager = None
    hub = None
    if settings.engine_deployment in {"docker", "distributed"}:
        local = DockerTenantManager(
            settings.engine_tenant_root or PROJECT / ".local/tenants",
            image=settings.engine_docker_image,
            production_cpus=settings.engine_production_cpus,
            production_memory=settings.engine_production_memory,
            development_cpus=settings.engine_development_cpus,
            development_memory=settings.engine_development_memory,
            log_root=(project if project is not None else PROJECT) / "logs",
            resource_limits=tenant_limits,
            research_enabled=settings.research_proxy_enabled,
        )
        manager = local
        if settings.engine_deployment == "distributed":
            if settings.node_config_file is None:
                raise ValueError("Distributed deployment requires NODE_CONFIG_FILE")
            hub = NodeHub(settings.node_config_file)
            manager = DistributedTenantManager(
                local, hub, image=settings.engine_docker_image,
                root=settings.engine_tenant_root or PROJECT / ".local/tenants",
                production_cpus=settings.engine_production_cpus,
                production_memory=settings.engine_production_memory,
                development_cpus=settings.engine_development_cpus,
                development_memory=settings.engine_development_memory,
                resource_limits=tenant_limits,
                development_node=development_node,
            )
        manager = ProductionDeploymentManager(
            manager, settings.engine_tenant_root or PROJECT / ".local/tenants", hub=hub,
        )
    if settings.harness_deployment not in {"single", "per_user"}:
        raise ValueError("HARNESS_DEPLOYMENT must be single or per_user")
    app.state.harness_instances = None
    if settings.harness_enabled and settings.harness_deployment == "per_user":
        if hub is None and settings.node_config_file is not None:
            hub = NodeHub(settings.node_config_file)
        app.state.harness_instances = UserHarnessInstances(
            root=settings.harness_root or PROJECT / ".local/harness-users",
            image=settings.harness_docker_image,
            cpus=settings.harness_cpus,
            memory=settings.harness_memory,
            hub=hub,
            placement=lambda user_id: app.state.user_resources.get(user_id)["harness"]["node"],
        )
    app.state.node_hub = hub
    app.state.production_deployments = manager
    app.state.engine = EngineClient(
        settings.database_path.parent / "engine", url=settings.engine_url, token=settings.engine_token,
        market_account_id=settings.engine_market_account_id,
        tenant_manager=manager,
    )
    app.state.olap = ClickhouseMonitor(olap_repository(database), app.state.accounts)
    app.state.operations = OperationService(database, app.state.accounts, app.state.engine)
    app.state.exchanges = ExchangeService(app.state.accounts, settings.database_path.parent / "portfolios",
                                          app.state.operations.repository, app.state.engine)
    app.state.equity = EquityService(database, app.state.accounts, app.state.engine)
    app.state.trading = ManualTrading(app.state.accounts, app.state.exchanges, app.state.operations,
                                      app.state.engine.root, app.state.engine)
    app.state.market_data = DataService(database)
    app.state.market_data.engine = app.state.engine
    app.state.tenants = TenantService(database, app.state.engine, app.state.accounts, app.state.user_resources)
    app.state.strategies = StrategyStore(database, app.state.engine, app.state.tenants)
    app.state.management = None
    app.state.management_project = (
        project if project is not None else PROJECT if settings.require_database_url else None
    )
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(settings.allowed_hosts))

    @app.middleware("http")
    async def request_protection(request: Request, call_next):
        origin = request.headers.get("origin")
        own_origin = f"{request.url.scheme}://{request.headers.get('host', '')}"
        if request.method not in {"GET", "HEAD", "OPTIONS"} and (
            (origin and origin != own_origin and origin not in settings.allowed_origins)
            or request.headers.get("sec-fetch-site") == "cross-site"
        ):
            response = JSONResponse({"detail": "请求来源不被允许。"}, status_code=403)
        else:
            response = await call_next(request)
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
            response.headers["Pragma"] = "no-cache"
        return response

    @app.exception_handler(EngineUnavailable)
    async def engine_unavailable(request, error):
        return JSONResponse({"detail": str(error)}, status_code=503)

    @app.exception_handler(QueryError)
    async def connector_error(request, error):
        return JSONResponse({"detail": str(error)}, status_code=502)

    @app.exception_handler(StorageUnavailable)
    async def storage_error(request, error):
        return JSONResponse({"detail": str(error)}, status_code=503)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, error):
        # Validation errors must not echo passwords, API secrets or submitted keys.
        return JSONResponse(
            {
                "detail": "请求参数无效。",
                "errors": [
                    {key: item[key] for key in ("loc", "msg", "type")} for item in error.errors()
                ],
            },
            status_code=422,
        )

    @app.get("/api/health", tags=["health"])
    def health():
        return {"status": "ok"}

    app.include_router(router, prefix="/api")
    app.include_router(users_router, prefix="/api")
    app.include_router(exchanges_router, prefix="/api")
    app.include_router(trading_router, prefix="/api")
    app.include_router(equity_router, prefix="/api")
    app.include_router(operations_router, prefix="/api")
    app.include_router(olap_router, prefix="/api")
    app.include_router(market_data_router, prefix="/api")
    app.include_router(management_router, prefix="/api")
    app.include_router(harness_router, prefix="/api")
    app.include_router(research_task_router, prefix="/api")
    app.include_router(tenants_router, prefix="/api")
    app.include_router(node_router, prefix="/api")
    app.include_router(strategies_router, prefix="/api")
    return app


app = create_app()
