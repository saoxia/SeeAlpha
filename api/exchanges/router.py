"""Frontend-compatible routes; all Binance I/O lives under connectors."""

import asyncio
import time
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request, Response

from core.engine_contracts import QueryError

from ..auth.dependencies import AdminUser, TradingUser
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
    user: TradingUser,
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
def accounts(request: Request, user: TradingUser):
    return request.app.state.accounts.list(user.id)


@router.post("/system/exchange-accounts", response_model=ExchangeAccountResponse, status_code=201)
def create_account(request: Request, payload: ExchangeAccountCreateRequest, user: TradingUser):
    return request.app.state.accounts.save(payload, owner_id=user.id)


@router.put("/system/exchange-accounts/{account_id}", response_model=ExchangeAccountResponse)
def update_account(
    request: Request, account_id: int, payload: ExchangeAccountUpdateRequest, user: TradingUser
):
    result = request.app.state.accounts.save(payload, account_id, owner_id=user.id)
    request.app.state.exchanges.invalidate(account_id)
    return result


@router.delete("/system/exchange-accounts/{account_id}", status_code=204)
def delete_account(request: Request, account_id: int, user: TradingUser):
    request.app.state.accounts.require_owned(account_id, user.id)
    request.app.state.accounts.repository.delete_exchange_account(account_id)
    request.app.state.exchanges.invalidate(account_id)
    return Response(status_code=204)


@router.post(
    "/system/exchange-accounts/{account_id}/test", response_model=ExchangeAccountTestResponse
)
async def test_account(request: Request, account_id: int, user: TradingUser):
    await asyncio.to_thread(request.app.state.accounts.require_owned, account_id, user.id)
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
async def balances(request: Request, user: TradingUser):
    items = []
    for account in await asyncio.to_thread(request.app.state.accounts.list, user.id):
        try:
            snapshot = await request.app.state.exchanges.snapshot(account["id"])
            items.append({"account": account, "balances": snapshot.balances, "error": None})
        except (QueryError, HTTPException) as exc:
            message = str(exc) if isinstance(exc, QueryError) else exc.detail
            items.append({"account": account, "balances": {}, "error": message})
    return {"checked_at": int(time.time()), "items": items}


@router.get("/exchange-accounts", response_model=list[ExchangeAccountNavigationResponse])
def navigation(request: Request, user: TradingUser):
    return request.app.state.accounts.navigation(user.id)


@router.get("/exchange-accounts/portfolio", response_model=ExchangePortfolioResponse)
async def overview(request: Request, user: TradingUser, refresh: bool = False):
    return await request.app.state.exchanges.overview(fresh=refresh, owner_id=user.id)


@router.get("/exchange-accounts/{account_id}/portfolio", response_model=ExchangePortfolioResponse)
async def portfolio(request: Request, account_id: int, user: TradingUser, refresh: bool = False):
    request.app.state.accounts.require_owned(account_id, user.id)
    return await request.app.state.exchanges.persisted_portfolio(account_id, fresh=refresh)


@router.get(
    "/exchange-accounts/{account_id}/positions", response_model=list[ExchangePositionResponse]
)
async def positions(request: Request, account_id: int, user: TradingUser):
    request.app.state.accounts.require_owned(account_id, user.id)
    values = (await request.app.state.exchanges.portfolio(account_id))["positions"]
    row = await asyncio.to_thread(request.app.state.accounts.row, account_id)
    options, _ = await asyncio.to_thread(position_view, request.app.state.operations.repository, row)
    return [*values, *options]


@router.get("/admin/exchange-accounts", response_model=list[ExchangeAccountNavigationResponse])
def administrator_account_inventory(request: Request, _: AdminUser):
    """Management-only account names for global ledger filters; no credentials."""
    return request.app.state.accounts.navigation()
