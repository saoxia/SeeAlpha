"""Frontend-compatible routes; all Binance I/O lives under connectors."""

import asyncio
import time
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request, Response

from core.engine_contracts import QueryError
from core.market.instruments import INTERVALS, MarketConfigError, normalize_symbol

from ..auth.dependencies import AdminUser, CurrentUser
from ..operations.options import position_view
from .contracts import (
    ExchangeAccountCreateRequest,
    ExchangeAccountNavigationResponse,
    ExchangeAccountResponse,
    ExchangeAccountTestResponse,
    ExchangeAccountUpdateRequest,
    ExchangeBalancesResponse,
    ExchangeMarketResponse,
    ExchangePortfolioResponse,
    ExchangePositionResponse,
)

router = APIRouter(tags=["exchanges"])


@router.get("/system/exchange-markets", response_model=list[ExchangeMarketResponse])
async def markets(
    request: Request,
    _: AdminUser,
    exchange: str = "binance",
    environment: Literal["production", "sandbox"] = "production",
    market_type: Literal["linear", "spot"] = "linear",
    search: str = Query(default="", max_length=80),
):
    if exchange != "binance":
        raise HTTPException(422, "暂不支持此交易所。")
    product = "spot" if market_type == "spot" else "futures_um"
    items = await request.app.state.exchanges.catalog(environment, product=product)
    return [i for i in items if search.strip().upper() in i["symbol"]][:200]


def checked_spot_symbol(symbol: str):
    try:
        return normalize_symbol(symbol, "spot")
    except MarketConfigError as exc:
        raise HTTPException(422, str(exc)) from None


@router.get("/system/exchange-spot-candles")
async def spot_candles(
    request: Request, _: CurrentUser,
    symbol: str = Query(min_length=3, max_length=30),
    interval: str = Query(default="1m", max_length=3),
    start_ms: int = Query(ge=0),
    end_ms: int = Query(gt=0),
    limit: int = Query(default=1000, ge=1, le=1000),
):
    """Public Spot OHLCV page, independent of the USD-M storage and trading paths."""
    symbol = checked_spot_symbol(symbol)
    if interval not in INTERVALS or start_ms >= end_ms:
        raise HTTPException(422, "现货周期或时间范围无效。")
    return await request.app.state.engine.spot_candles(
        symbol, interval, start_ms, end_ms, limit=limit,
    )


@router.get("/system/exchange-spot-recent-trades")
async def spot_recent_trades(
    request: Request, _: CurrentUser,
    symbol: str = Query(min_length=3, max_length=30),
    limit: int = Query(default=500, ge=1, le=1000),
):
    """Recent public trades; never claim a complete historical trade set."""
    symbol = checked_spot_symbol(symbol)
    return await request.app.state.engine.spot_recent_trades(symbol, limit=limit)


@router.get("/system/exchange-accounts", response_model=list[ExchangeAccountResponse])
def accounts(request: Request, _: AdminUser):
    return request.app.state.accounts.list()


@router.post("/system/exchange-accounts", response_model=ExchangeAccountResponse, status_code=201)
def create_account(request: Request, payload: ExchangeAccountCreateRequest, _: AdminUser):
    return request.app.state.accounts.save(payload)


@router.put("/system/exchange-accounts/{account_id}", response_model=ExchangeAccountResponse)
def update_account(
    request: Request, account_id: int, payload: ExchangeAccountUpdateRequest, _: AdminUser
):
    result = request.app.state.accounts.save(payload, account_id)
    request.app.state.exchanges.invalidate(account_id)
    return result


@router.delete("/system/exchange-accounts/{account_id}", status_code=204)
def delete_account(request: Request, account_id: int, _: AdminUser):
    request.app.state.accounts.row(account_id)
    request.app.state.accounts.repository.delete_exchange_account(account_id)
    request.app.state.exchanges.invalidate(account_id)
    return Response(status_code=204)


@router.post(
    "/system/exchange-accounts/{account_id}/test", response_model=ExchangeAccountTestResponse
)
async def test_account(request: Request, account_id: int, _: AdminUser):
    await asyncio.to_thread(request.app.state.accounts.row, account_id)
    try:
        await request.app.state.exchanges.snapshot(account_id, fresh=True)
    except QueryError:
        await asyncio.to_thread(
            request.app.state.accounts.repository.update_exchange_account_test_result,
            account_id,
            succeeded=False,
        )
        raise
    await asyncio.to_thread(
        request.app.state.accounts.repository.update_exchange_account_test_result,
        account_id,
        succeeded=True,
    )
    return {"ok": True, "message": "Binance 原生账户余额与仓位查询成功。"}


@router.get("/system/exchange-accounts/balances", response_model=ExchangeBalancesResponse)
async def balances(request: Request, _: AdminUser):
    items = []
    for account in await asyncio.to_thread(request.app.state.accounts.list):
        try:
            snapshot = await request.app.state.exchanges.snapshot(account["id"])
            items.append({"account": account, "balances": snapshot.balances, "error": None})
        except (QueryError, HTTPException) as exc:
            message = str(exc) if isinstance(exc, QueryError) else exc.detail
            items.append({"account": account, "balances": {}, "error": message})
    return {"checked_at": int(time.time()), "items": items}


@router.get("/exchange-accounts", response_model=list[ExchangeAccountNavigationResponse])
def navigation(request: Request, _: CurrentUser):
    return request.app.state.accounts.navigation()


@router.get("/exchange-accounts/portfolio", response_model=ExchangePortfolioResponse)
async def overview(request: Request, _: CurrentUser, refresh: bool = False):
    return await request.app.state.exchanges.overview(fresh=refresh)


@router.get("/exchange-accounts/{account_id}/portfolio", response_model=ExchangePortfolioResponse)
async def portfolio(request: Request, account_id: int, _: CurrentUser, refresh: bool = False):
    return await request.app.state.exchanges.persisted_portfolio(account_id, fresh=refresh)


@router.get(
    "/exchange-accounts/{account_id}/positions", response_model=list[ExchangePositionResponse]
)
async def positions(request: Request, account_id: int, _: CurrentUser):
    values = (await request.app.state.exchanges.portfolio(account_id))["positions"]
    row = await asyncio.to_thread(request.app.state.accounts.row, account_id)
    options, _ = await asyncio.to_thread(position_view, request.app.state.operations.repository, row)
    return [*values, *options]
