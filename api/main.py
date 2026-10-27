"""Independent API entry point; no legacy business workers are started here."""

import asyncio
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from starlette.responses import JSONResponse

from connectors.binance.queries import QueryError

from .auth.router import router
from .config import Settings
from .db.auth_repository import auth_repository
from .db.database import create_database
from .db.errors import StorageUnavailable
from .exchanges.accounts import Accounts
from .exchanges.equity_sample import router as equity_sample_router
from .exchanges.router import router as exchanges_router
from .exchanges.service import ExchangeService
from .olap.repository import olap_repository
from .olap.router import router as olap_router
from .olap.service import ClickhouseMonitor
from .operations.router import router as operations_router
from .operations.service import OperationService


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_environment()
    database = create_database(settings)

    @asynccontextmanager
    async def lifespan(app):
        task = None
        olap_task = None
        try:
            database.initialize()
            app.state.operations.repository.initialize()
            app.state.olap.repository.initialize()

            async def refresh_olap():
                while True:
                    await asyncio.to_thread(app.state.olap.refresh_freshness_cache)
                    await asyncio.sleep(60)

            olap_task = asyncio.create_task(refresh_olap())
            if settings.operation_sync_seconds:
                task = asyncio.create_task(
                    app.state.operations.worker(settings.operation_sync_seconds)
                )
            yield
        finally:
            if olap_task is not None:
                olap_task.cancel()
                with suppress(asyncio.CancelledError):
                    await olap_task
            if task is not None:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
            database.close()

    app = FastAPI(title="SeeAlpha API", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.repository = auth_repository(database)
    app.state.database = database
    app.state.accounts = Accounts(database, settings)
    app.state.olap = ClickhouseMonitor(olap_repository(database), app.state.accounts)
    app.state.exchanges = ExchangeService(app.state.accounts)
    app.state.operations = OperationService(database, app.state.accounts)
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
    app.include_router(equity_sample_router, prefix="/api")
    app.include_router(operations_router, prefix="/api")
    app.include_router(olap_router, prefix="/api")
    return app


app = create_app()
