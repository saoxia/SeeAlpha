"""Read the project ledger; Binance is an ingestion source, never the list endpoint."""

import base64
import csv
import io
import json
import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from starlette.responses import StreamingResponse

from ..auth.dependencies import AdminUser, CurrentUser
from .models import EventType, Source, SyncRequest

router = APIRouter(prefix="/operations", tags=["operations"])


def filters(
    account_id: int | None = Query(None, gt=0),
    environment: Literal["production", "sandbox"] | None = None,
    strategy_id: str | None = Query(None, min_length=1, max_length=200),
    source: Source | None = None,
    event_type: EventType | None = None,
    symbol: str | None = Query(None, min_length=1, max_length=100),
    start_ms: int | None = Query(None, gt=0),
    end_ms: int | None = Query(None, gt=0),
):
    if start_ms and end_ms and start_ms > end_ms:
        raise HTTPException(422, "开始时间不能晚于结束时间。")
    return locals()


def encode_cursor(record):
    return (
        base64.urlsafe_b64encode(
            json.dumps([record["timestamp"], record["id"]], separators=(",", ":")).encode()
        )
        .decode()
        .rstrip("=")
    )


def decode_cursor(value):
    if value is None:
        return None
    try:
        result = json.loads(
            base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
        )
        if (
            not isinstance(result, list)
            or len(result) != 2
            or type(result[0]) is not int
            or not 0 < result[0] <= 9_000_000_000_000_000
            or not isinstance(result[1], str)
        ):
            raise ValueError
        result[1] = str(uuid.UUID(result[1]))
        return result
    except (ValueError, TypeError, UnicodeError):
        raise HTTPException(422, "无效的操作记录分页游标。") from None


@router.get("")
def list_operations(
    request: Request,
    _: CurrentUser,
    query: Annotated[dict, Depends(filters)],
    cursor: str | None = Query(None, max_length=200),
    limit: int = Query(50, ge=1, le=500),
):
    repo = request.app.state.operations.repository
    result = repo.list(cursor=decode_cursor(cursor), limit=limit, **query)
    result["next_cursor"] = (
        encode_cursor(result["records"][-1]) if result["has_more"] and result["records"] else None
    )
    result["sync"] = repo.sync_state(query["account_id"])
    result["warnings"] = [
        "操作记录为项目与交易所证据的去重并集；未匹配记录归为外部，不自动认定为手工。"
    ]
    if any(s["status"] == "failed" for s in result["sync"]):
        result["warnings"].append("部分账户同步失败，当前列表可能不完整。")
    if not result["sync"]:
        result["warnings"].append("尚无交易所同步覆盖记录；空列表不代表账户没有历史操作。")
    elif any(not s["complete"] for s in result["sync"]):
        result["warnings"].append("交易所历史覆盖不完整，详见同步状态。")
    return result


@router.post("/sync")
async def sync_operations(request: Request, payload: SyncRequest, _: AdminUser):
    return await request.app.state.operations.sync(**payload.model_dump())


@router.get("/export")
def export_operations(request: Request, _: CurrentUser, query: Annotated[dict, Depends(filters)]):
    repo = request.app.state.operations.repository
    fields = [
        "id",
        "account_id",
        "account_name",
        "environment",
        "exchange",
        "symbol",
        "event_type",
        "timestamp",
        "source",
        "strategy_id",
        "client_order_id",
        "order_id",
        "trade_id",
        "amount",
        "price",
        "fee_cost",
        "fee_currency",
        "status",
        "confirmed",
    ]

    def chunks():
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        yield "\ufeff" + buffer.getvalue()
        buffer.seek(0)
        buffer.truncate(0)
        cursor = None
        while True:
            page = repo.list(cursor=cursor, limit=500, **query)
            for row in page["records"]:
                writer.writerow(
                    {
                        k: (
                            "'" + v
                            if isinstance(v, str) and v.startswith(("=", "+", "-", "@"))
                            else v
                        )
                        for k, v in row.items()
                    }
                )
            yield buffer.getvalue()
            buffer.seek(0)
            buffer.truncate(0)
            if not page["has_more"]:
                break
            last = page["records"][-1]
            cursor = [last["timestamp"], last["id"]]

    return StreamingResponse(
        chunks(),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="project-operations.csv"'},
    )


@router.get("/{operation_id}")
def operation_detail(request: Request, operation_id: uuid.UUID, _: CurrentUser):
    result = request.app.state.operations.repository.detail(str(operation_id))
    if result is None:
        raise HTTPException(404, "操作记录不存在。")
    return result
