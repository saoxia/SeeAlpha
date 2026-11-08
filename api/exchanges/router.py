"""Frontend-compatible routes; all Binance I/O lives under connectors."""

import asyncio
import time
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request, Response

from connectors.binance.queries import QueryError

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
    market_type: str = "linear",
    search: str = Query(default="", max_length=80),
):
    if exchange != "binance" or market_type != "linear":
        raise HTTPException(422, "本轮原生接入仅开放 Binance USD-M 永续市场。")
    items = await request.app.state.exchanges.catalog(environment)
    return [i for i in items if search.strip().upper() in i["symbol"]][:200]


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
