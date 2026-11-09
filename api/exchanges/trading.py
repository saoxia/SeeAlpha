"""Authenticated manual trading with durable request identity and account serialization."""

import asyncio
import hashlib
import json
import time
from pathlib import Path
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from core.engine_contracts import EngineConflict
from core.trading_contracts import CancelOrderValues, SubmitOrderValues, TRADING_SYMBOL

from ..auth.dependencies import AdminUser
from ..db.errors import StorageConflict, StorageUnavailable

router = APIRouter(prefix="/trading", tags=["trading"])
SYMBOL = TRADING_SYMBOL


class ManualOrderRequest(SubmitOrderValues):
    pass


class ManualCancelRequest(CancelOrderValues):
    pass


class ManualOrderView(BaseModel):
    client_order_id: str
    order_id: str | None
    symbol: str
    side: str
    order_type: str
    quantity: str
    filled: str
    price: str | None
    trigger_price: str | None
    status: str
    reduce_only: bool
    timestamp: int
    open: bool


class ManualPosition(BaseModel):
    position_side: Literal["BOTH", "LONG", "SHORT"] = "BOTH"
    break_even_price: str | None = None
    liquidation_price: str | None = None
    id: str
    symbol: str
    side: str
    quantity: str
    entry_price: str


class ManualBalance(BaseModel):
    total: str
    free: str
    locked: str


class ManualInstrument(BaseModel):
    symbol: str
    price_increment: str
    size_increment: str
    min_quantity: str | None
    min_notional: str | None


class ManualStateResponse(BaseModel):
    leverage: int | None = Field(default=None, gt=0, strict=True)
    position_mode: Literal["oneway", "hedge"]
    account_revision: str
    checked_at: int
    orders: list[ManualOrderView]
    positions: list[ManualPosition]
    balances: dict[str, ManualBalance]
    instrument: ManualInstrument


class ManualMarketSummary(BaseModel):
    symbol: str
    base: str
    quote: Literal["USDT", "USDC"]
    last_price: str | None
    change_percent: str | None
    quote_volume: str | None
    funding_rate: str | None


class ManualMarketsResponse(BaseModel):
    checked_at: int
    environment: Literal["production", "sandbox"]
    markets: list[ManualMarketSummary]


class ManualOrderResponse(BaseModel):
    request_id: str
    client_order_id: str | None = None
    status: Literal["completed", "unknown"]
    message: str
    order: ManualOrderView | None = None


def account_revision(account):
    identity = [account[key] for key in ("id", "environment", "credentials_encrypted", "updated_at")]
    return hashlib.sha256(json.dumps(identity).encode()).hexdigest()


class ManualTrading:
    def __init__(self, accounts, exchanges, operations, root, engine):
        self.accounts, self.exchanges, self.operations = accounts, exchanges, operations
        self.root = Path(root)
        self.engine = engine
        self.cache, self.locks = {}, {}

    async def snapshot(self, account_id, symbol):
        async with self.locks.setdefault(account_id, asyncio.Lock()):
            account = await asyncio.to_thread(self.accounts.private, account_id)
            identity = (account_id, account["environment"], account["updated_at"], account["credentials_encrypted"], symbol)
            cached = self.cache.get(identity)
            if cached and time.monotonic() - cached[0] < 10:
                return cached[1]
            result = await self.engine.trading_state(account, symbol)
            for record in result.pop("records", []):
                await asyncio.to_thread(self.operations.repository.ingest, record)
            result["account_revision"] = account_revision(account)
            self.cache[identity] = (time.monotonic(), result)
            return result

    async def _sync_ledger(self, tenant_id, account_id, stored):
        if stored.get("ledger_synced") or not stored.get("records"):
            return
        request_id = stored["response"]["request_id"]
        try:
            for record in stored["records"]:
                await asyncio.to_thread(self.operations.repository.ingest, record)
        except (StorageUnavailable, StorageConflict):
            stored["response"]["message"] += "；执行证据已保存在引擎，账本暂未同步。"
        else:
            self.engine.mark_ledger_synced(tenant_id, account_id, request_id)

    async def command(self, account_id, payload, action):
        # The engine reserves request_id in SQLite before any native node can send.
        async with self.locks.setdefault(account_id, asyncio.Lock()):
            account = await asyncio.to_thread(self.accounts.private, account_id)
            if payload.environment != account["environment"]:
                raise HTTPException(409, "账户环境已变化，请重新选择账户并确认。")
            if payload.account_revision != account_revision(account):
                raise HTTPException(409, "账户配置已变化，请刷新账户并重新确认。")
            values = payload.model_dump(mode="json")
            try:
                stored = await self.engine.command(account, action, values)
            except EngineConflict as exc:
                raise HTTPException(409, exc.message) from None
            if not stored.get("replay"):
                self.cache.clear()
                self.exchanges.invalidate(account_id)
            await self._sync_ledger(account["tenant_id"], account_id, stored)
            return stored["response"]


@router.get("/accounts/{account_id}/state", response_model=ManualStateResponse)
async def state(request: Request, account_id: int, _: AdminUser,
                symbol: str = Query(pattern=SYMBOL)):
    return await request.app.state.trading.snapshot(account_id, symbol)


@router.post("/accounts/{account_id}/orders", response_model=ManualOrderResponse)
async def submit(request: Request, account_id: int, payload: ManualOrderRequest, _: AdminUser):
    return await asyncio.shield(request.app.state.trading.command(account_id, payload, "submit"))


@router.post("/accounts/{account_id}/cancel", response_model=ManualOrderResponse)
async def cancel(request: Request, account_id: int, payload: ManualCancelRequest, _: AdminUser):
    return await asyncio.shield(request.app.state.trading.command(account_id, payload, "cancel"))


@router.get("/accounts/{account_id}/requests/{request_id}", response_model=ManualOrderResponse)
async def lookup(request: Request, account_id: int, request_id: UUID, _: AdminUser,
                 environment: Literal["production", "sandbox"]):
    service = request.app.state.trading
    account = await asyncio.to_thread(service.accounts.private, account_id)
    if account["environment"] != environment:
        raise HTTPException(409, "账户环境已变化，不能用新环境核对原委托。")
    stored = service.engine.get_request(account["tenant_id"], account_id, request_id)
    if stored is None:
        raise HTTPException(404, "服务器没有该请求记录，请在交易所核对后处理。")
    if stored["response"]["status"] == "unknown":
        revision = account_revision(account)
        if stored["account_revision"] != revision:
            raise HTTPException(409, "账户配置已变化，不能使用新凭据核对原委托。")
        try:
            stored = await service.engine.reconcile_request(account, request_id, revision)
        except EngineConflict as exc:
            raise HTTPException(409, exc.message) from None
    return stored["response"]

@router.get("/markets", response_model=ManualMarketsResponse)
async def market_list(request: Request, _: AdminUser,
                      environment: Literal["production", "sandbox"] = "production"):
    catalog, snapshot = await asyncio.gather(request.app.state.exchanges.catalog(environment),
                                             request.app.state.engine.market_tickers(environment))
    rows = []
    for item in catalog:
        if item["quote"] not in {"USDT", "USDC"}:
            continue
        raw_symbol = item["base"] + item["quote"]
        rows.append({"symbol": raw_symbol + "-PERP", "base": item["base"], "quote": item["quote"],
                     **snapshot["rows"].get(raw_symbol, {"last_price": None, "change_percent": None,
                                                       "quote_volume": None, "funding_rate": None})})
    return {"checked_at": snapshot["checked_at"], "environment": environment, "markets": rows}
