"""Authenticated real equity candles and administrator replay commands."""

import time
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..auth.dependencies import AdminUser, CurrentUser
from ..exchanges.contracts import EquityCandleSeriesResponse

router = APIRouter(tags=["equity"])
Interval = Literal["5m", "15m", "1h", "4h", "1d"]
INTERVAL_SECONDS = {"5m": 300, "15m": 900, "1h": 3600, "4h": 14400, "1d": 86400}


class EquityRebuildRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start_ms: int | None = Field(default=None, gt=0)
    end_ms: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def valid_range(self):
        now = int(time.time() * 1000)
        if (self.end_ms is not None and self.end_ms > now
                or self.start_ms is not None and self.start_ms > (self.end_ms or now)):
            raise ValueError("重算起止时间无效或晚于当前时间。")
        return self


class EquityRebuildResponse(BaseModel):
    id: str
    account_id: int
    environment: str
    status: str
    start_ms: int | None = None
    end_ms: int
    created_at: int
    processed: int = 0
    gaps: int = 0
    finished_at: int | None = None
    message: str | None = None


def candles(points, seconds, limit):
    buckets, gaps = {}, set()
    for stamp, _, point in points:
        bucket = stamp // (seconds * 1000) * seconds * 1000
        if point["total_equity"] is None:
            gaps.add(bucket)
            continue
        number = Decimal(point["total_equity"])
        if bucket not in buckets:
            buckets[bucket] = {"bucket_start": bucket, "open": number, "high": number,
                               "low": number, "close": number, "sample_count": 0}
        candle = buckets[bucket]
        candle.update(high=max(candle["high"], number), low=min(candle["low"], number), close=number,
                      sample_count=candle["sample_count"] + 1)
    # A coarse bucket containing an explicit gap cannot promise a valid OHLC.
    result = [{k: str(v) if k in {"open", "high", "low", "close"} else v for k, v in c.items()}
              for stamp, c in sorted(buckets.items()) if stamp not in gaps]
    return result[-limit:], sorted(gaps)[-limit:]


def aligned_points(timeline, count, start):
    # Aggregate the aligned point timeline, never sum independent OHLC bars.
    latest = {}
    for stamp, event_id, body in timeline:
        latest[body["account_id"]] = (stamp, body)
        if stamp < start:
            continue
        ready = len(latest) == count and all(
            stamp - when <= 30_000 and item["total_equity"] is not None
            for when, item in latest.values())
        total = sum((Decimal(item["total_equity"]) for _, item in latest.values()), Decimal(0)) if ready else None
        yield stamp, event_id, {"total_equity": str(total) if ready else None}


def series(request, rows, interval, limit, account_id=None, name="总览"):
    seconds = INTERVAL_SECONDS[interval]
    now = int(time.time() * 1000)
    start = (now // (seconds * 1000) - limit + 1) * seconds * 1000
    result = {"account_id": account_id, "account_name": name, "currency": "USDT",
              "metric": "total_equity", "interval_seconds": seconds, "is_sample": False,
              "candles": [], "gaps": [], "warnings": [], "status": "pending"}
    repository = request.app.state.equity.repository
    if repository.database.backend != "orm":
        result.update(status="unavailable", warnings=["资产曲线需要已迁移的 ORM 业务数据库。"])
        return result
    if not rows:
        result["warnings"] = ["尚未配置可估值账户。"]
        return result
    if len({row["environment"] for row in rows}) > 1:
        result.update(status="unavailable", warnings=["正式与测试账户历史不能合并，请选择单个账户。"])
        return result
    timeline = repository.points([(row["id"], row["environment"]) for row in rows], start - 30_000, now)
    if len(rows) == 1:
        points = (point for point in timeline if point[0] >= start)
    else:
        points = aligned_points(timeline, len(rows), start)
    result["candles"], result["gaps"] = candles(points, seconds, limit)
    states = [repository.state(row["id"], row["environment"]) for row in rows]
    ready = all(s["status"] == "ready" and now - s.get("last_event_at", 0) <= 30_000 for s in states)
    result["status"] = "ready" if ready else "pending" if not any(s.get("last_event_at") for s in states) else "gap"
    result["warnings"] = ["仅展示启用后保存证据覆盖的估值；空白区间不代表资产为零。"]
    if not ready:
        result["warnings"].append("账户尚未就绪、价格过期或采集已中断。")
    return result


@router.get("/exchange-accounts/equity-candles", response_model=EquityCandleSeriesResponse)
def overview(request: Request, _: CurrentUser, interval: Interval = "5m", limit: int = Query(120, ge=1, le=500)):
    rows = [row for row in request.app.state.accounts.repository.list_exchange_accounts()
            if row["exchange"] == "binance"]
    return series(request, rows, interval, limit)


@router.get("/exchange-accounts/{account_id}/equity-candles", response_model=EquityCandleSeriesResponse)
def account(request: Request, account_id: int, _: CurrentUser, interval: Interval = "5m",
            limit: int = Query(120, ge=1, le=500)):
    row = request.app.state.accounts.row(account_id)
    return series(request, [row], interval, limit, account_id, row["account_name"])


@router.get("/exchange-accounts/{account_id}/equity-status")
def status(request: Request, account_id: int, _: CurrentUser):
    row = request.app.state.accounts.row(account_id)
    state = request.app.state.equity.repository.state(account_id, row["environment"])
    if state["status"] == "ready" and int(time.time() * 1000) - state.get("last_event_at", 0) > 30_000:
        state.update(status="gap", message="资产采集已超过新鲜度窗口；最近估值仅作历史参考。")
    return state


@router.post("/exchange-accounts/{account_id}/equity-rebuilds", status_code=202,
             response_model=EquityRebuildResponse)
async def rebuild(request: Request, account_id: int, payload: EquityRebuildRequest, _: AdminUser):
    row = request.app.state.accounts.row(account_id)
    return await request.app.state.equity.rebuild(row, payload.start_ms, payload.end_ms or int(time.time() * 1000))


@router.get("/exchange-accounts/{account_id}/equity-rebuilds/{job_id}", response_model=EquityRebuildResponse)
def rebuild_status(request: Request, account_id: int, job_id: str, _: CurrentUser):
    row = request.app.state.accounts.row(account_id)
    job = request.app.state.equity.repository.job(job_id)
    if job["account_id"] != account_id or job["environment"] != row["environment"]:
        raise HTTPException(404, "资产重算任务不存在。")
    return job
