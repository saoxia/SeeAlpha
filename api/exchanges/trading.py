"""Authenticated manual trading with durable request identity and account serialization."""

import asyncio
import fcntl
import hashlib
import json
import os
import time
from decimal import Decimal
from pathlib import Path
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field, model_validator

from connectors.binance import manual
from connectors.binance.queries import QueryError

from ..auth.dependencies import AdminUser
from ..db.errors import StorageConflict, StorageUnavailable

router = APIRouter(prefix="/trading", tags=["trading"])
SYMBOL = r"^[A-Z0-9]{2,30}(?:USDT|USDC)-PERP$"


class ManualOrderRequest(BaseModel):
    account_revision: str = Field(pattern=r"^[a-f0-9]{64}$")
    request_id: UUID
    symbol: str = Field(pattern=SYMBOL)
    side: Literal["buy", "sell"]
    order_type: Literal["limit", "market"]
    quantity: str = Field(pattern=r"^\d{1,16}(?:\.\d{1,18})?$", max_length=35)
    price: str | None = Field(default=None, pattern=r"^\d{1,16}(?:\.\d{1,18})?$", max_length=35)
    time_in_force: Literal["GTC", "IOC", "FOK"] = "GTC"
    post_only: bool = False
    reduce_only: bool = False
    position_side: Literal["BOTH", "LONG", "SHORT"] | None = Field(default=None, description="Legacy explicit leg; new tickets use position_effect and the actual account mode.")
    position_effect: Literal["open", "close"] = "open"
    expected_position_mode: Literal["oneway", "hedge"] | None = None
    environment: Literal["production", "sandbox"]

    @model_validator(mode="after")
    def validate_order(self):
        if Decimal(self.quantity) <= 0:
            raise ValueError("数量必须大于零。")
        if self.order_type == "limit" and (self.price is None or Decimal(self.price) <= 0):
            raise ValueError("限价单需要有效价格。")
        if self.order_type == "market" and (self.price is not None or self.post_only or self.time_in_force != "GTC"):
            raise ValueError("市价单不能设置限价、Post Only 或限价有效期。")
        if self.post_only and self.time_in_force != "GTC":
            raise ValueError("Post Only 仅支持 GTC。")
        if self.position_side in {"LONG", "SHORT"} and self.reduce_only:
            raise ValueError("双向持仓请按持仓方向和买卖方向平仓，不能传 Reduce Only。")
        return self


class ManualCancelRequest(BaseModel):
    account_revision: str = Field(pattern=r"^[a-f0-9]{64}$")
    request_id: UUID
    symbol: str = Field(pattern=SYMBOL)
    client_order_id: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9._:/-]+$")
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
    def __init__(self, accounts, exchanges, operations, root):
        self.accounts, self.exchanges, self.operations = accounts, exchanges, operations
        self.root = Path(root)
        self.cache, self.locks = {}, {}

    async def snapshot(self, account_id, symbol):
        async with self.locks.setdefault(account_id, asyncio.Lock()):
            account = await asyncio.to_thread(self.accounts.private, account_id)
            identity = (account_id, account["environment"], account["updated_at"], account["credentials_encrypted"], symbol)
            cached = self.cache.get(identity)
            if cached and time.monotonic() - cached[0] < 10:
                return cached[1]
            result = await manual.execute(account, symbol, {"action": "snapshot"})
            for record in result.pop("records", []):
                await asyncio.to_thread(self.operations.repository.ingest, record)
            result["account_revision"] = account_revision(account)
            self.cache[identity] = (time.monotonic(), result)
            return result

    def write(self, path, value):
        temporary = path.with_suffix(".tmp")
        with temporary.open("w", encoding="utf-8") as file:
            os.chmod(temporary, 0o600)
            json.dump(value, file, ensure_ascii=False)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)

    async def persist_observations(self, path, stored):
        if stored.get("ledger_synced") or not stored.get("records"):
            return
        try:
            for record in stored["records"]:
                await asyncio.to_thread(self.operations.repository.ingest, record)
        except (StorageUnavailable, StorageConflict):
            stored["response"]["message"] += "；执行证据已保存在本机，账本暂未同步。"
        else:
            stored["ledger_synced"] = True
        self.write(path, stored)

    async def command(self, account_id, payload, action):
        # A detached task completes even when the browser disconnects. The durable
        # identity remains reserved before the native node can send anything.
        async with self.locks.setdefault(account_id, asyncio.Lock()):
            account = await asyncio.to_thread(self.accounts.private, account_id)
            if payload.environment != account["environment"]:
                raise HTTPException(409, "账户环境已变化，请重新选择账户并确认。")
            if payload.account_revision != account_revision(account):
                raise HTTPException(409, "账户配置已变化，请刷新账户并重新确认。")
            directory = self.root / str(account_id) / account["environment"]
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            with (directory / "account.lock").open("a") as lock:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    raise HTTPException(409, "该账户有交易操作正在处理，请稍后核对委托。") from None
                path = directory / f"{payload.request_id}.json"
                values = payload.model_dump(mode="json")
                fingerprint = hashlib.sha256(json.dumps([action, values], sort_keys=True).encode()).hexdigest()
                if path.exists():
                    stored = json.loads(path.read_text())
                    if stored["fingerprint"] != fingerprint:
                        raise HTTPException(409, "请求编号已用于另一笔操作。")
                    await self.persist_observations(path, stored)
                    return stored["response"]
                key = "SA" + payload.request_id.hex if action == "submit" else payload.client_order_id
                response = {"request_id": str(payload.request_id), "client_order_id": key,
                            "status": "unknown", "order": None,
                            "message": "请求已登记，交易结果待核实。请刷新委托，不要重复提交。"}
                stored = {"fingerprint": fingerprint, "response": response, "symbol": payload.symbol,
                          "account_revision": payload.account_revision}
                self.write(path, stored)
                try:
                    result = await manual.execute(account, payload.symbol,
                                                  {**values, "action": action, "client_order_id": key})
                    if "validation_error" in result:
                        response.update(status="completed", message=result["validation_error"])
                        self.write(path, stored)
                        return response
                    order = result["result"]
                    records = result.pop("records", [])
                    # Preserve captured execution evidence before ledger ingestion;
                    # a database outage must never trigger a second exchange write.
                    stored["records"] = records
                    if order is not None:
                        response.update(status="completed", order=order,
                                        message=("撤单被拒绝，当前订单状态：" if result.get("outcome") == "OrderCancelRejected" else "交易所订单状态：") + order["status"])
                    self.write(path, stored)
                    await self.persist_observations(path, stored)
                except QueryError:
                    response["message"] = "交易结果待核实，请刷新委托并按客户订单号核对，不要重复提交。"
                    self.write(path, stored)
                finally:
                    self.cache.clear()
                    self.exchanges.invalidate(account_id)
                return response


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
    path = service.root / str(account_id) / environment / f"{request_id}.json"
    if not path.exists():
        raise HTTPException(404, "服务器没有该请求记录，请在交易所核对后处理。")
    stored = json.loads(path.read_text())
    response = stored["response"]
    if response["status"] == "unknown":
        if stored["account_revision"] != account_revision(account):
            raise HTTPException(409, "账户配置已变化，不能使用新凭据核对原委托。")
        snapshot = await service.snapshot(account_id, stored["symbol"])
        order = next((o for o in snapshot["orders"] if o["client_order_id"] == response["client_order_id"]), None)
        if order:
            # A fresh venue observation resolves uncertainty, never re-send here.
            response = {**response, "status": "completed", "order": order,
                        "message": "已在交易所核对到委托：" + order["status"]}
    return response


@router.get("/markets", response_model=ManualMarketsResponse)
async def market_list(request: Request, _: AdminUser,
                      environment: Literal["production", "sandbox"] = "production"):
    catalog, snapshot = await asyncio.gather(request.app.state.exchanges.catalog(environment),
                                             manual.market_tickers(environment))
    rows = []
    for item in catalog:
        if item["quote"] not in {"USDT", "USDC"}:
            continue
        raw_symbol = item["base"] + item["quote"]
        rows.append({"symbol": raw_symbol + "-PERP", "base": item["base"], "quote": item["quote"],
                     **snapshot["rows"].get(raw_symbol, {"last_price": None, "change_percent": None,
                                                       "quote_volume": None, "funding_rate": None})})
    return {"checked_at": snapshot["checked_at"], "environment": environment, "markets": rows}
