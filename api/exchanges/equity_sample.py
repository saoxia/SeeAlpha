"""Temporary illustrative equity candles, unrelated to live account balances."""

import math
import time
from typing import Literal

from fastapi import APIRouter, Query, Request

from ..auth.dependencies import CurrentUser
from .contracts import EquityCandleSeriesResponse

router = APIRouter(tags=["exchanges"])
Interval = Literal["5m", "15m", "1h", "4h", "1d"]
INTERVAL_SECONDS = {"5m": 300, "15m": 900, "1h": 3600, "4h": 14400, "1d": 86400}


def sample_series(account_id: int | None, name: str, interval: Interval, limit: int):
    seconds = INTERVAL_SECONDS[interval]
    end = int(time.time()) // seconds * seconds
    candles = []
    for index in range(min(limit, 80)):
        bucket = end - (min(limit, 80) - index - 1) * seconds
        # The same bucket yields the same candle across requests and time ranges.
        step = bucket // seconds

        def value(n):
            return 10000 + 180 * math.sin(n / 19) + 70 * math.sin(n / 5)

        opening, closing = value(step), value(step + 1)
        candles.append(
            {
                "bucket_start": bucket * 1000,
                "open": f"{opening:.2f}",
                "high": f"{max(opening, closing) + 15:.2f}",
                "low": f"{min(opening, closing) - 15:.2f}",
                "close": f"{closing:.2f}",
                "sample_count": 1,
            }
        )
    return {
        "account_id": account_id,
        "account_name": name,
        "currency": "USD",
        "metric": "total_equity",
        "interval_seconds": seconds,
        "is_sample": True,
        "candles": candles,
    }


@router.get("/exchange-accounts/equity-candles", response_model=EquityCandleSeriesResponse)
def overview(_: CurrentUser, interval: Interval = "5m", limit: int = Query(120, ge=1, le=500)):
    return sample_series(None, "总览", interval, limit)


@router.get(
    "/exchange-accounts/{account_id}/equity-candles", response_model=EquityCandleSeriesResponse
)
def account(
    request: Request,
    account_id: int,
    _: CurrentUser,
    interval: Interval = "5m",
    limit: int = Query(120, ge=1, le=500),
):
    row = request.app.state.accounts.row(account_id)
    return sample_series(account_id, row["account_name"], interval, limit)
