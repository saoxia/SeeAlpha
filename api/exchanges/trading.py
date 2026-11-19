"""Authenticated manual trading with durable request identity and account serialization."""

import asyncio
import hashlib
import json
import time
from decimal import Decimal
from pathlib import Path
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator

from core.engine_contracts import EngineConflict
from core.trading_contracts import (
    TRADING_SYMBOL,
    AlgoCancelValues,
    AlgoOrderValues,
    CancelOrderValues,
    LeverageChangeValues,
    SubmitOrderValues,
)

from ..auth.dependencies import TradingUser
from ..db.errors import StorageConflict, StorageUnavailable

router = APIRouter(prefix="/trading", tags=["trading"])
SYMBOL = TRADING_SYMBOL


class ManualOrderRequest(SubmitOrderValues):
    pass


class ManualCancelRequest(CancelOrderValues):
    pass


class SpotOrderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    account_revision: str = Field(pattern=r"^[a-f0-9]{64}$")
    request_id: UUID
    symbol: str = Field(pattern=r"^[A-Z0-9]{2,30}(?:USDT|USDC|FDUSD)$")
    side: Literal["buy", "sell"]
    order_type: Literal["limit", "market"]
    quantity: str = Field(pattern=r"^\d{1,16}(?:\.\d{1,18})?$", max_length=35)
    price: str | None = Field(default=None, pattern=r"^\d{1,16}(?:\.\d{1,18})?$", max_length=35)
    time_in_force: Literal["GTC", "IOC", "FOK"] = "GTC"
    post_only: bool = False
    environment: Literal["production", "sandbox"]

    @model_validator(mode="after")
    def check_spot(self):
        if Decimal(self.quantity) <= 0:
            raise ValueError("现货数量必须大于零")
        if self.order_type == "limit" and (self.price is None or Decimal(self.price) <= 0):
            raise ValueError("现货限价单需要有效价格")
        if self.order_type == "market" and (
            self.price is not None or self.post_only or self.time_in_force != "GTC"
        ):
            raise ValueError("现货市价单不能设置限价或 Post Only")
        if self.post_only and self.time_in_force != "GTC":
            raise ValueError("Post Only 仅支持 GTC")
        return self


class SpotCancelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    account_revision: str = Field(pattern=r"^[a-f0-9]{64}$")
    request_id: UUID
    symbol: str = Field(pattern=r"^[A-Z0-9]{2,30}(?:USDT|USDC|FDUSD)$")
    client_order_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    environment: Literal["production", "sandbox"]


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



class ManualAlgoOrderView(BaseModel):
    client_algo_id: str
    algo_id: str | None = None
    symbol: str
    side: Literal["buy", "sell"]
    position_side: Literal["BOTH", "LONG", "SHORT"]
    order_type: Literal["STOP", "STOP_MARKET", "TAKE_PROFIT", "TAKE_PROFIT_MARKET", "TRAILING_STOP_MARKET"]
    quantity: str
    price: str | None = None
    trigger_price: str | None = None
    activation_price: str | None = None
    callback_rate: str | None = None
    working_type: str
    status: str
    open: bool
    timestamp: int


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
    algo_orders: list[ManualAlgoOrderView] | None = None
    algo_checked_at: int | None = None
    algo_error: str | None = None
    positions: list[ManualPosition]
    balances: dict[str, ManualBalance]
    instrument: ManualInstrument


class ManualMarketSummary(BaseModel):
    symbol: str
    base: str
    quote: Literal["USDT", "USDC", "FDUSD"]
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
    algo_order: ManualAlgoOrderView | None = None
    leverage: int | None = None


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
async def state(request: Request, account_id: int, user: TradingUser,
                symbol: str = Query(pattern=SYMBOL)):
    request.app.state.accounts.require_owned(account_id, user.id)
    return await request.app.state.trading.snapshot(account_id, symbol)

@router.get("/accounts/{account_id}/spot/state", response_model=ManualStateResponse)
async def spot_state(request: Request, account_id: int, user: TradingUser,
                     symbol: str = Query(pattern=r"^[A-Z0-9]{2,30}(?:USDT|USDC|FDUSD)$")):
    request.app.state.accounts.require_owned(account_id, user.id)
    return await request.app.state.trading.snapshot(account_id, symbol)


@router.post("/accounts/{account_id}/orders", response_model=ManualOrderResponse)
async def submit(request: Request, account_id: int, payload: ManualOrderRequest, user: TradingUser):
    request.app.state.accounts.require_owned(account_id, user.id)
    return await asyncio.shield(request.app.state.trading.command(account_id, payload, "submit"))

@router.post("/accounts/{account_id}/spot/orders", response_model=ManualOrderResponse)
async def spot_submit(request: Request, account_id: int, payload: SpotOrderRequest, user: TradingUser):
    request.app.state.accounts.require_owned(account_id, user.id)
    return await asyncio.shield(request.app.state.trading.command(account_id, payload, "submit"))


@router.post("/accounts/{account_id}/cancel", response_model=ManualOrderResponse)
async def cancel(request: Request, account_id: int, payload: ManualCancelRequest, user: TradingUser):
    request.app.state.accounts.require_owned(account_id, user.id)
    return await asyncio.shield(request.app.state.trading.command(account_id, payload, "cancel"))

@router.post("/accounts/{account_id}/spot/cancel", response_model=ManualOrderResponse)
async def spot_cancel(request: Request, account_id: int, payload: SpotCancelRequest, user: TradingUser):
    request.app.state.accounts.require_owned(account_id, user.id)
    return await asyncio.shield(request.app.state.trading.command(account_id, payload, "cancel"))



@router.post("/accounts/{account_id}/algo/orders", response_model=ManualOrderResponse)
async def algo_submit(request: Request, account_id: int, payload: AlgoOrderValues, user: TradingUser):
    request.app.state.accounts.require_owned(account_id, user.id)
    return await asyncio.shield(request.app.state.trading.command(account_id, payload, "algo_submit"))


@router.post("/accounts/{account_id}/algo/cancel", response_model=ManualOrderResponse)
async def algo_cancel(request: Request, account_id: int, payload: AlgoCancelValues, user: TradingUser):
    request.app.state.accounts.require_owned(account_id, user.id)
    return await asyncio.shield(request.app.state.trading.command(account_id, payload, "algo_cancel"))


@router.post("/accounts/{account_id}/leverage", response_model=ManualOrderResponse)
async def set_leverage(request: Request, account_id: int, payload: LeverageChangeValues, user: TradingUser):
    request.app.state.accounts.require_owned(account_id, user.id)
    return await asyncio.shield(request.app.state.trading.command(account_id, payload, "leverage"))


@router.get("/accounts/{account_id}/requests/{request_id}", response_model=ManualOrderResponse)
async def lookup(request: Request, account_id: int, request_id: UUID, user: TradingUser,
                 environment: Literal["production", "sandbox"]):
    request.app.state.accounts.require_owned(account_id, user.id)
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


@router.get("/liquidation-model")
async def liquidation_model(user: TradingUser, symbol: str = Query(pattern=SYMBOL)):
    """Trusted external model only; does not infer nonexistent market liquidation data."""
    from .liquidation_model import fetch_model
    return await fetch_model(symbol)


@router.get("/chart-signals")
def chart_signals(
    request: Request,
    user: TradingUser,
    tenant_id: int = Query(gt=0),
    virtual_account_id: int = Query(gt=0),
    symbol: str = Query(pattern=SYMBOL),
):
    """User-authorized read-only target changes from a recorded development backtest."""
    from decimal import Decimal, InvalidOperation

    request.app.state.tenants.require_owned(tenant_id, user.id)
    result = request.app.state.strategies.latest_backtest(tenant_id, virtual_account_id)
    if result.get("symbol") != symbol.removesuffix("-PERP"):
        return {"source": "backtest_target", "signals": [], "message": "回测标的与图表不一致。"}
    previous = {}
    markers = []
    for index, item in enumerate(result.get("target_events", [])[-2000:]):
        if not isinstance(item, dict) or item.get("symbol") != symbol.removesuffix("-PERP"):
            continue
        try:
            target = Decimal(str(item["quantity"]))
            stamp = item["time"]
        except (KeyError, InvalidOperation, TypeError, ValueError):
            continue
        if (not target.is_finite() or type(stamp) is not int or stamp <= 0
                or not isinstance(item.get("strategy_id"), str)):
            continue
        strategy_id = item["strategy_id"]
        before = previous.get(strategy_id, Decimal(0))
        previous[strategy_id] = target
        if target == before:
            continue
        markers.append({
            "id": f"backtest:{tenant_id}:{virtual_account_id}:{index}",
            "time": stamp, "direction": "buy" if target > before else "sell",
            "label": "回测目标增加" if target > before else "回测目标减少",
            "source": "strategy",
        })
    return {"source": "backtest_target", "signals": markers[-500:],
            "message": "仅显示已授权开发回测的历史目标变化；不是实时信号或成交。"}


@router.get("/markets", response_model=ManualMarketsResponse)
async def market_list(request: Request, user: TradingUser,
                      environment: Literal["production", "sandbox"] = "production",
                      product: Literal["futures_um", "spot"] = "futures_um"):
    catalog, snapshot = await asyncio.gather(
        request.app.state.exchanges.catalog(environment, product="spot") if product == "spot"
        else request.app.state.exchanges.catalog(environment),
        request.app.state.engine.market_tickers(environment, "spot") if product == "spot"
        else request.app.state.engine.market_tickers(environment),
    )
    rows = []
    for item in catalog:
        if item["quote"] not in {"USDT", "USDC", "FDUSD"}:
            continue
        raw_symbol = item["base"] + item["quote"]
        rows.append({"symbol": raw_symbol + ("-PERP" if product == "futures_um" else ""),
                     "base": item["base"], "quote": item["quote"],
                     **snapshot["rows"].get(raw_symbol, {"last_price": None, "change_percent": None,
                                                       "quote_volume": None, "funding_rate": None})})
    return {"checked_at": snapshot["checked_at"], "environment": environment, "markets": rows}
