"""Independent API entry point. Research jobs start here; no legacy console is imported."""

import asyncio
import logging
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from starlette.responses import JSONResponse

from engine.connectors.binance.queries import QueryError

from .auth.router import router
from .config import PROJECT, Settings
from .db.auth_repository import auth_repository
from .db.database import create_database
from .db.errors import StorageUnavailable
from .engine_client import EngineClient
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
from .olap.repository import olap_repository
from .olap.router import router as olap_router
from .olap.service import ClickhouseMonitor
from .operations.router import router as operations_router
from .operations.service import OperationService
from .tenants.router import router as tenants_router
from .tenants.service import TenantService


def create_app(settings: Settings | None = None, *, project: Path | None = None) -> FastAPI:
    settings = settings or Settings.from_environment()
    database = create_database(settings)

    @asynccontextmanager
    async def lifespan(app):
        task = None
        olap_task = None
        configuration_task = None
        monitor_task = None
        portfolio_task = None
        runtime = None
        try:
            database.initialize()
            default_tenant = app.state.tenants.repository.require_default()
            if default_tenant is not None:
                app.state.engine.ensure_tenant(
                    default_tenant["id"], default_tenant["purpose"], default_tenant["status"]
                )
            if app.state.management_project is not None:
                # Another process may already hold the management lock. Startup
                # still continues; only that holder runs and recovers jobs.
                runtime = management_runtime(app.state.management_project, database)
                app.state.management = runtime.__enter__()
            app.state.operations.repository.initialize()
            app.state.olap.repository.initialize()
            await asyncio.to_thread(app.state.market_data.subscriptions.restore)
            await app.state.market_data.subscriptions.sync_options(app.state.exchanges)

            async def reconcile_configuration():
                while True:
                    await asyncio.sleep(15)
                    await asyncio.to_thread(app.state.market_data.subscriptions.restore)
                    await app.state.market_data.subscriptions.sync_options(app.state.exchanges)

            async def monitor_services():
                while True:
                    try:
                        await asyncio.to_thread(app.state.market_data.monitor.sample)
                    except OSError:
                        logging.getLogger(__name__).warning("行情监控快照写入失败，将稍后重试。")
                    await asyncio.sleep(5)

            configuration_task = asyncio.create_task(reconcile_configuration())
            monitor_task = asyncio.create_task(monitor_services())
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
            for background in (configuration_task, monitor_task, portfolio_task):
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
    app.state.repository = auth_repository(database)
    app.state.database = database
    app.state.accounts = Accounts(database, settings)
    app.state.engine = EngineClient(
        settings.database_path.parent / "engine", url=settings.engine_url, token=settings.engine_token,
        market_account_id=settings.engine_market_account_id,
    )
    app.state.olap = ClickhouseMonitor(olap_repository(database), app.state.accounts)
    app.state.operations = OperationService(database, app.state.accounts)
    app.state.exchanges = ExchangeService(app.state.accounts, settings.database_path.parent / "portfolios",
                                          app.state.operations.repository, app.state.engine)
    app.state.equity = EquityService(database, app.state.accounts, app.state.engine)
    app.state.trading = ManualTrading(app.state.accounts, app.state.exchanges, app.state.operations,
                                      app.state.engine.root, app.state.engine)
    app.state.market_data = DataService(database)
    app.state.market_data.engine = app.state.engine
    app.state.tenants = TenantService(database, app.state.engine, app.state.accounts)
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
    app.include_router(exchanges_router, prefix="/api")
    app.include_router(trading_router, prefix="/api")
    app.include_router(equity_router, prefix="/api")
    app.include_router(operations_router, prefix="/api")
    app.include_router(olap_router, prefix="/api")
    app.include_router(market_data_router, prefix="/api")
    app.include_router(management_router, prefix="/api")
    app.include_router(tenants_router, prefix="/api")
    return app


app = create_app()
