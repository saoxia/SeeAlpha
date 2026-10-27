import asyncio

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from ..auth.dependencies import AdminUser, CurrentUser
from .service import ClickhouseConfigurationError, ClickhouseConnectionError

router = APIRouter(tags=["olap"])


class OlapSettingsRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    address: str = Field(min_length=1, max_length=500)
    port: int = Field(ge=1, le=65535)
    database: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    username: str = Field(min_length=1, max_length=128)
    password: str | None = Field(default=None, max_length=1024, repr=False)


class TableRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    table: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")


def translate(exc):
    return HTTPException(400 if isinstance(exc, ClickhouseConfigurationError) else 502, str(exc))


@router.get("/system/olap")
def get_settings(request: Request, _: AdminUser):
    return request.app.state.olap.public_olap_settings()


@router.put("/system/olap")
def put_settings(request: Request, payload: OlapSettingsRequest, _: AdminUser):
    try:
        return request.app.state.olap.save_olap_settings(
            address=payload.address,
            port=payload.port,
            database_name=payload.database,
            username=payload.username,
            password=payload.password,
        )
    except ClickhouseConfigurationError as exc:
        raise translate(exc) from exc


@router.post("/system/olap/test")
async def test_settings(request: Request, _: AdminUser):
    try:
        tables = await asyncio.to_thread(request.app.state.olap.available_tables)
        return {
            "ok": True,
            "message": "ClickHouse 连接成功，UTC 时间监控可用。",
            "table_count": len(tables),
        }
    except (ClickhouseConfigurationError, ClickhouseConnectionError) as exc:
        raise translate(exc) from exc


@router.get("/clickhouse/tables")
async def tables(request: Request, _: CurrentUser):
    try:
        return await asyncio.to_thread(request.app.state.olap.available_tables)
    except (ClickhouseConfigurationError, ClickhouseConnectionError) as exc:
        raise translate(exc) from exc


@router.get("/clickhouse/monitor/status")
def status(request: Request, _: CurrentUser):
    return request.app.state.olap.cached_freshness_status()


@router.post("/clickhouse/monitor/refresh")
async def refresh(request: Request, _: CurrentUser):
    return await asyncio.to_thread(request.app.state.olap.refresh_freshness_cache)


@router.post("/clickhouse/monitor/tables", status_code=201)
async def add_table(request: Request, payload: TableRequest, _: CurrentUser):
    try:
        request.app.state.olap.add_monitored_table(payload.table)
        return await asyncio.to_thread(request.app.state.olap.refresh_freshness_cache)
    except ClickhouseConfigurationError as exc:
        raise translate(exc) from exc


@router.delete("/clickhouse/monitor/tables/{table}")
async def delete_table(request: Request, table: str, _: CurrentUser):
    try:
        request.app.state.olap.remove_monitored_table(table)
        return await asyncio.to_thread(request.app.state.olap.refresh_freshness_cache)
    except ClickhouseConfigurationError as exc:
        raise translate(exc) from exc
