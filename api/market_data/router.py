"""Data operations are authenticated and reuse fixed pipeline entry points."""

import asyncio
import json
import time
from contextlib import suppress
from dataclasses import replace
from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Request,
    WebSocket,
    WebSocketDisconnect,
)
from starlette.concurrency import run_in_threadpool
from starlette.responses import FileResponse

from core.market.instruments import INTERVALS, bar_window
 from ..auth.dependencies import COOKIE_NAME, AdminUser, CurrentUser, require_admin, require_user
from ..db.errors import StorageUnavailable
from .contracts import (
    ChartCandle,
    DataCapabilities,
    DataDeletePlanRequest,
    DataDeletionPlan,
    DataJobRequest,
    DataJobResponse,
    DataMarket,
    DataPolicy,
    DataPolicySave,
    DataRange,
    LocalChartResponse,
    PairDeletePlanRequest,
    PairDeleteRequest,
    option_pair,
)

def load_settings():
    from engine.pipeline.config import load_settings as load_native_settings

    return load_native_settings()


def dataset_inventory(*args, **kwargs):
    from engine.pipeline.data_management import dataset_inventory as query

    return query(*args, **kwargs)


def coverage(*args, **kwargs):
    from engine.pipeline.data_management import coverage as query

    return query(*args, **kwargs)


def preview(*args, **kwargs):
    from engine.pipeline.data_management import preview as query

    return query(*args, **kwargs)


def rows(*args, **kwargs):
    from engine.pipeline.data_management import rows as query

    return query(*args, **kwargs)


router = APIRouter(prefix="/data", tags=["market-data"])


def service(request: Request):
    return request.app.state.market_data


def query_range(symbol: str, start: datetime, end: datetime, interval: str = "1m",
                product: Literal["futures_um", "options"] = "futures_um"):
    try:
        return DataRange(symbol=symbol, start=start, end=end, intervals=[] if interval == "tick" else [interval], product=product)
    except ValueError:
        raise HTTPException(422, "品种、时间范围或周期无效；时间须带时区，结束时间须晚于开始时间。") from None


@router.get("/capabilities", response_model=DataCapabilities)
def capabilities(request: Request, user: CurrentUser):
    settings = load_settings()
    manager = service(request)
    return {"market": settings.market, "symbols": [],
            "intervals": list(INTERVALS),
            "can_operate": (user.username.casefold() == request.app.state.settings.admin_username.casefold()
                            and manager.policy_repository is not None)}


@router.get("/markets", response_model=list[DataMarket])
async def data_markets(request: Request, _: AdminUser, product: Literal["futures_um", "options"] = "futures_um"):
    if product == "options":
        return [{**item, "product": "options"} for item in await request.app.state.exchanges.catalog("production", product="options")]
    items = await request.app.state.exchanges.catalog("production")
    return [{"symbol": f"{item['base']}{item['quote']}", "base": item["base"], "quote": item["quote"]}
            for item in items if item["quote"] in {"USDT", "USDC"}]


async def require_download_market(request, payload):
    symbol = payload.symbol
    if payload.product == "options":
        if symbol == "ALL" and isinstance(payload, DataPolicy):
            return
        items = await request.app.state.exchanges.catalog("production", product="options")
        configured = any(p.symbol == symbol and p.product == "options"
                         for p in service(request).subscriptions._read())
        match = next((item for item in items if item["symbol"] == symbol), None)
        if match and isinstance(payload, DataPolicy):
            payload.underlying = match.get("underlying") or option_pair(symbol)
        if not configured and not any(item["symbol"] == symbol or isinstance(payload, DataPolicy)
                                      and (item.get("underlying") or option_pair(item["symbol"])) == symbol for item in items):
            raise HTTPException(422, "该合约不在 Binance 当前期权目录中。")
        return
    items = await request.app.state.exchanges.catalog("production")
    if not any(f"{item['base']}{item['quote']}" == symbol and item["quote"] in {"USDT", "USDC"} for item in items):
        raise HTTPException(422, "该交易对不在 Binance 当前支持的 U 本位永续品种中。")


@router.get("/status")
def status(request: Request, _: CurrentUser):
    manager = service(request)
    storage = manager.storage()
    operation_error = None
    try:
        tasks = manager.jobs()
    except StorageUnavailable as exc:
        tasks, operation_error = [], str(exc)
    return {"storage": storage, "tasks": tasks, "operation_error": operation_error,
            "realtime_ticks": {"state": "per_pair", "persisted": True}, "monitor": manager.monitor.read()}


@router.get("/datasets")
def datasets(request: Request, _: CurrentUser, interval: str | None = None, all_levels: bool = False):
    manager = service(request)
    if getattr(manager, "engine", None) is not None and manager.engine.remote:
        symbols = [p.symbol for p in manager.subscriptions._read() if p.product == "futures_um"]
        return manager.engine.data_query("datasets", {
            "interval": interval, "all_levels": all_levels, "symbols": symbols,
        })
    manager.require_storage()
    settings = load_settings()
    if interval is not None and interval not in INTERVALS:
        raise HTTPException(422, "K 线周期未配置")
    settings = replace(settings, intervals=tuple(INTERVALS))
    items = dataset_inventory(settings, interval=interval, all_levels=all_levels)
    from engine.feed.pair_store import journal_inventory
    for policy in service(request).subscriptions._read():
        with service(request)._lease(policy.symbol, policy.product, access=True, shared=True):
            journal_items = [item for path in service(request).subscriptions.journal_paths(policy) for item in journal_inventory(path, policy.symbol)]
        for item in journal_items:
            if not all_levels and item["level"] != (interval or "tick"):
                continue
            existing = next((value for value in items if value["symbol"] == item["symbol"] and value["level"] == item["level"] and value.get("product", "futures_um") == "futures_um"), None)
            if existing:
                existing["event_ranges_ns"] = sorted([*existing["event_ranges_ns"], *item["event_ranges_ns"]], key=lambda bounds: int(bounds[0]))
            else:
                items.append(item)
    return {"items": items}


@router.get("/datasets/latest", response_model=dict[str, str | None])
def latest_data_times(request: Request, _: CurrentUser):
    """Latest persisted 1s bar event times for configured perpetual pairs only."""
    manager = service(request)
    symbols = [policy.symbol for policy in manager.subscriptions._read()
               if policy.product == "futures_um"]
    if getattr(manager, "engine", None) is not None and manager.engine.remote:
        return manager.engine.data_query("latest", {"symbols": symbols})
    from nautilus_trader.model import NautilusDataType

    from core.market.instruments import bar_type_for
    from engine.pipeline.data_management import open_catalog

    manager.require_storage()
    settings = load_settings()
    if not symbols or not settings.catalog_dir.is_dir():
        return dict.fromkeys(symbols)
    catalog = open_catalog(settings)
    result = {}
    for symbol in symbols:
        with manager._lease(symbol, "futures_um", access=True, shared=True):
            stamp = catalog.native.query_last_timestamp(NautilusDataType.Bar, bar_type_for(symbol, "1s"))
        result[symbol] = str(stamp) if stamp is not None else None
    return result


@router.get("/coverage")
def data_coverage(request: Request, _: CurrentUser, payload: Annotated[DataRange, Depends(query_range)]):
    manager = service(request)
    if getattr(manager, "engine", None) is not None and manager.engine.remote:
        return manager.engine.data_query("coverage", payload.model_dump(mode="json"))
    manager.require_storage()
    try:
        selection, settings = manager.request(payload)
        return coverage(selection, settings)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None


@router.post("/prepare/plan")
async def plan(request: Request, payload: DataRange, _: AdminUser):
    await require_download_market(request, payload)
    manager = service(request)
    if getattr(manager, "engine", None) is not None and manager.engine.remote:
        return await run_in_threadpool(manager.engine.data_query, "prepare-plan", payload.model_dump(mode="json"))
    try:
        return await run_in_threadpool(manager.plan, payload)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None


@router.get("/ticks")
def ticks(request: Request, _: CurrentUser, payload: Annotated[DataRange, Depends(query_range)],
          limit: int = Query(100, ge=1, le=500), cursor: str | None = Query(None, max_length=2000)):
    return preview_data(request, payload, limit, cursor)


@router.post("/delete/plan", response_model=DataDeletionPlan)
def delete_plan(request: Request, payload: DataDeletePlanRequest, _: AdminUser):
    # Deletion preview and executor must share the same filesystem identity.
    # Keep both on the API host until the destructive job migrates as one unit.
    try:
        return service(request).delete_plan(payload)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None


@router.get("/chart", response_model=LocalChartResponse)
def local_chart(request: Request, _: CurrentUser,
                symbol: str = Query(pattern=r"^[A-Z0-9]+(?:USDT|USDC)$"),
                interval: Literal["1m", "3m", "5m", "15m", "30m", "1h", "4h", "1d"] = "1m"):
    """Bounded display projection only; never provides strategy readiness."""
    manager = service(request)
    if getattr(manager, "engine", None) is not None and manager.engine.remote:
        policy = next((p for p in manager.subscriptions._read()
                       if p.symbol == symbol and p.product == "futures_um"), None)
        return manager.engine.data_query("chart", {
            "symbol": symbol, "interval": interval,
            "base_level": policy.base_level if policy is not None else None,
        })
    from engine.feed.pair_store import journal_chart
    manager.require_storage()
    now = int(time.time() * 1000)
    left = bar_window(interval, now)[0]
    width = int(INTERVALS[interval][2].total_seconds() * 1000)
    start = left - 299 * width
    policy = next((p for p in manager.subscriptions._read()
                   if p.symbol == symbol and p.product == "futures_um"), None)
    candles = {}
    live = False
    with manager._lease(symbol, "futures_um", access=True, shared=True):
        settings = load_settings()
        if settings.catalog_dir.is_dir():
            source = rows(settings, symbol, datetime.fromtimestamp(start / 1000, UTC),
                          datetime.fromtimestamp(now / 1000, UTC), interval=interval)
            try:
                for row in source:
                    stamp = int(row["ts_event_ns"]) // 1000000
                    opening, _ = bar_window(interval, stamp)
                    candles[opening] = ChartCandle(time=opening // 1000, is_closed=True,
                        **{key: row[key] for key in ("open", "high", "low", "close", "volume")})
            finally:
                source.close()
        if policy:
            values = {}
            # Prefer the active retention over journals from older configurations.
            for path in reversed(manager.subscriptions.journal_paths(policy)):
                for value in journal_chart(path, interval, start, now + 1):
                    values[value["start_ms"]] = value
            state = manager.subscriptions._public(policy)
            collection = state["services"]["collection"]
            calculation = state["services"]["calculation"]
            live = bool(collection.get("healthy") and calculation.get("healthy"))
            for observation in (collection, calculation):
                if observation.get("healthy"):
                    for value in observation.get("bar_updates", []):
                        if value.get("interval") == interval and start <= value["start_ms"] <= now:
                            previous = values.get(value["start_ms"])
                            if previous and (previous["updated_ms"] > value["updated_ms"]
                                             or previous["is_closed"] and not value["is_closed"]):
                                continue
                            values[value["start_ms"]] = value
            for opening, value in values.items():
                candles[opening] = ChartCandle(time=opening // 1000,
                    is_complete=value["is_complete"], is_closed=value["is_closed"],
                    **{key: value[key] for key in ("open", "high", "low", "close", "volume")})
    return LocalChartResponse(symbol=symbol, interval=interval, checked_at=now, live=live,
                              candles=[candles[key] for key in sorted(candles)[-300:]])


@router.get("/bars")
def bars(request: Request, _: CurrentUser, payload: Annotated[DataRange, Depends(query_range)],
         limit: int = Query(100, ge=1, le=500), cursor: str | None = Query(None, max_length=2000)):
    if not payload.intervals:
        raise HTTPException(422, "K 线预览需要指定周期")
    return preview_data(request, payload, limit, cursor, interval=payload.intervals[0])


def preview_data(request, payload, limit, cursor, interval=None):
    manager = service(request)
    if getattr(manager, "engine", None) is not None and manager.engine.remote:
        subscribed = any(
            p.symbol == payload.symbol and p.product == payload.product
            for p in manager.subscriptions._read()
        )
        return manager.engine.data_query("page", {
            **payload.model_dump(mode="json"), "interval": interval,
            "limit": limit, "cursor": cursor, "journal": subscribed,
        })
    manager.require_storage()
    try:
        settings = load_settings()
        policies = manager.subscriptions._read()
        policy = next((value for value in policies if value.symbol == payload.symbol and value.product == payload.product), None)
        with manager._lease(payload.symbol, payload.product, access=True, shared=True):
            if policy:
                from engine.feed.pair_store import journal_page
                for path in manager.subscriptions.journal_paths(policy):
                    try:
                        page = journal_page(path, interval or "tick", int(payload.start.timestamp() * 1000),
                                            int(payload.end.timestamp() * 1000), limit,
                                            cursor if cursor and cursor.startswith("journal.") else None)
                    except ValueError:
                        if cursor and cursor.startswith("journal."):
                            continue  # The opaque cursor may belong to another retained minimum.
                        raise
                    if page["records"] or cursor and cursor.startswith("journal."):
                        return page
                if cursor and cursor.startswith("journal."):
                    raise ValueError("游标无效或数据已变化，请重新查询")
            return preview(settings, payload.symbol, payload.start, payload.end,
                           limit=limit, cursor=cursor, interval=interval, market=payload.product)
    except FileNotFoundError:
        raise HTTPException(404, "Catalog 尚不存在，请先准备数据。") from None
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None


@router.post("/jobs", response_model=DataJobResponse, status_code=202)
async def create_job(request: Request, payload: DataJobRequest, _: AdminUser):
    if payload.action == "prepare":
        await require_download_market(request, payload)
    try:
        return await run_in_threadpool(service(request).start, payload)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None


@router.get("/jobs", response_model=list[DataJobResponse])
def jobs(request: Request, _: CurrentUser):
    return service(request).jobs()


@router.get("/jobs/{identity}", response_model=DataJobResponse)
def job(request: Request, identity: str, _: AdminUser):
    return service(request).job(identity)


@router.post("/jobs/{identity}/retry", response_model=DataJobResponse, status_code=202)
def retry_job(request: Request, identity: str, _: AdminUser):
    return service(request).retry(identity)


@router.post("/jobs/{identity}/cancel", response_model=DataJobResponse)
def cancel(request: Request, identity: str, _: AdminUser):
    return service(request).cancel(identity)


@router.get("/jobs/{identity}/download")
def download(request: Request, identity: str, _: AdminUser):
    manager = service(request)
    value = manager.job(identity)
    if value["status"] != "succeeded" or value["action"] != "export":
        raise HTTPException(409, "该任务没有已完成的导出文件")
    if manager.remote:
        from fastapi.responses import StreamingResponse

        return StreamingResponse(
            manager.engine.market_export(identity),
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="ticks-{identity}.csv"'},
        )
    manager.require_storage()
    root = manager.project / "data/exports/ticks"
    path = root / f"{identity}.csv"
    if (root.is_symlink() or root.parent.is_symlink() or path.is_symlink()
            or not path.is_file() or path.resolve().parent != root.resolve()):
        raise HTTPException(404, "导出文件不可用")
    return FileResponse(path, media_type="text/csv", filename=f"ticks-{identity}.csv")


@router.get("/quality")
def quality(request: Request, _: CurrentUser, payload: Annotated[DataRange, Depends(query_range)]):
    manager = service(request)
    manager.require_storage()
    try:
        selection = payload if manager.remote else manager.request(payload)[0]
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    for value in manager.jobs():
        parameters = value["parameters"]
        if (value["action"] == "validate" and parameters["symbol"] ==
                (selection.symbol if manager.remote else selection.symbols[0])
                and parameters.get("product", "futures_um") == payload.product
                and (payload.product != "options" or parameters.get("intervals") == payload.intervals)
                and parameters["start"] == payload.model_dump(mode="json")["start"]
                and parameters["end"] == payload.model_dump(mode="json")["end"]):
            return {"status": value["status"], "job_id": value["id"], "result": value["result"],
                    "message": "历史校验结果；数据后续变化会使其失效，验收前须重新校验。"}
    return {"status": "unverified", "job_id": None, "result": None,
            "message": "此范围尚未执行逐笔校验。"}


@router.get("/feed/status")
def feed_status(request: Request, _: CurrentUser):
    manager = service(request)
    if manager.remote:
        return manager.engine.market_control("feed-status")
    manager.require_storage()
    path = manager.project / "data/market_data/binance_um_1m/status.json"
    if not path.is_file():
        return {"state": "STOPPED", "data_ready": False, "reason": "not_started",
                "data_type": "minute_ohlcv", "checked_ms": None, "valid_until_ms": None}
    try:
        value = json.loads(path.read_text())
        if (not isinstance(value, dict) or type(value.get("valid_until_ms")) is not int
                or type(value.get("checked_ms")) is not int
                or value.get("state") not in {"READY", "DEGRADED", "WARMING_UP", "STOPPED"}):
            raise ValueError
        expired = int(time.time() * 1000) > value["valid_until_ms"]
        raw_symbols = value.get("symbols", {})
        if not isinstance(raw_symbols, dict):
            raise TypeError
        symbols = {}
        for symbol, progress in raw_symbols.items():
            if not isinstance(progress, dict):
                raise TypeError
            symbols[symbol] = {}
            for field in ("next_ms", "last_websocket_ms"):
                stamp = progress.get(field)
                if stamp is not None and (type(stamp) is not int or stamp < 0):
                    raise ValueError
                symbols[symbol][field] = stamp
        return {"state": "DEGRADED" if expired else value["state"],
                "data_ready": not expired and value["state"] == "READY" and value.get("data_ready") is True,
                "reason": "status_expired" if expired else value.get("reason"),
                "checked_ms": value["checked_ms"], "valid_until_ms": value["valid_until_ms"],
                "symbols": symbols,
                "data_type": "minute_ohlcv"}
    except (OSError, ValueError, TypeError):
        return {"state": "DEGRADED", "data_ready": False, "reason": "invalid_status",
                "data_type": "minute_ohlcv", "checked_ms": None, "valid_until_ms": None}


@router.websocket("/jobs/stream")
async def task_stream(websocket: WebSocket, _: AdminUser, job_id: str = ""):
    """Read-only snapshots over the existing same-origin session and task store."""
    settings = websocket.app.state.settings
    origin = websocket.headers.get("origin")
    same_origin = f"{websocket.url.scheme.replace('ws', 'http')}://{websocket.headers.get('host')}"
    if (not origin or origin not in {same_origin, *settings.allowed_origins}
            or websocket.headers.get("sec-fetch-site") == "cross-site"):
        await websocket.close(code=1008)
        return
    if job_id and (len(job_id) != 32 or any(c not in "0123456789abcdef" for c in job_id)):
        await websocket.close(code=1008)
        return
    await websocket.accept()
    previous = None
    try:
        while True:
            # Revocation/expiry is checked on every snapshot, including idle connections.
            def snapshot():
                user = require_user(websocket, websocket.cookies.get(COOKIE_NAME))
                require_admin(websocket, user)
                return websocket.app.state.market_data.task_snapshot(job_id)

            value = await run_in_threadpool(snapshot)
            await websocket.send_json(value if value != previous else {"type": "heartbeat"})
            previous = value
            try:
                event = await asyncio.wait_for(websocket.receive(), timeout=1)
                if event["type"] == "websocket.disconnect":
                    break
                # This is a read-only channel; clients never submit task commands here.
                await websocket.close(code=1008)
                break
            except TimeoutError:
                pass
    except (WebSocketDisconnect, OSError):
        pass
    except HTTPException:
        with suppress(WebSocketDisconnect, OSError):
            await websocket.close(code=1008)
    except StorageUnavailable:
        with suppress(WebSocketDisconnect, OSError):
            await websocket.close(code=1013)


@router.get("/subscriptions")
def subscriptions(request: Request, _: CurrentUser, group_options: bool = False):
    return {"items": service(request).subscriptions.items(group_options=group_options)}


@router.get("/services")
def service_status(request: Request, _: CurrentUser):
    return service(request).monitor.read()


@router.post("/pairs/delete/plan", response_model=DataDeletionPlan)
def pair_delete_plan(request: Request, payload: PairDeletePlanRequest, _: AdminUser):
    try:
        return service(request).pair_delete_plan(payload)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None


@router.post("/pairs/delete", response_model=DataJobResponse, status_code=202)
def delete_pair(request: Request, payload: PairDeleteRequest, _: AdminUser):
    try:
        return service(request).start(payload)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None


@router.post("/subscriptions/plan", response_model=dict)
async def configuration_plan(request: Request, payload: DataPolicy, _: AdminUser):
    if payload.realtime_enabled or payload.history_enabled:
        await require_download_market(request, payload)
    try:
        return await run_in_threadpool(service(request).configuration_plan, payload)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None


@router.post("/subscriptions", response_model=dict)
async def save_subscription(request: Request, payload: DataPolicySave, _: AdminUser):
    # Offline configuration/stop must remain available if the venue catalogue is down.
    if payload.realtime_enabled or payload.history_enabled:
        await require_download_market(request, payload)
    manager = service(request)
    policy = DataPolicy.model_validate(payload.model_dump(exclude={"deletion_snapshot", "confirmation"}))
    try:
        result = await run_in_threadpool(manager.save_configuration, policy,
                                        deletion_snapshot=payload.deletion_snapshot, confirmation=payload.confirmation)
        if policy.product == "options" and "-" not in policy.symbol:
            await manager.subscriptions.sync_options(request.app.state.exchanges)
        return result
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
