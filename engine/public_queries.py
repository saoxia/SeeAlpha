"""Typed, tenant-scoped Engine HTTP endpoints for venue read-only queries.

No exchange credentials or connector objects are returned to the business API.
Decimals are serialized as strings to preserve trading/asset precision.
"""
import asyncio
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from engine.connectors.binance import assets, manual, queries

router = APIRouter(prefix="/tenants/{tenant_id}/venue", tags=["engine-venue"])


def wire(value):
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, dict):
        return {str(key): wire(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [wire(item) for item in value]
    return value


class PublicRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    environment: Literal["production", "sandbox"] = "production"
    product: Literal["futures_um", "options"] = "futures_um"


class PriceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    environment: Literal["production", "sandbox"]
    currency: str = Field(pattern=r"^[A-Z0-9]{1,30}$")


class AccountRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    environment: Literal["production", "sandbox"]
    api_key: str = Field(min_length=1)
    api_secret: str = Field(min_length=1)
    account_id: str
    account_name: str = ""
    exchange: str = "binance"
    positions: bool = False

    def account(self, tenant_id):
        return {
            "tenant_id": tenant_id,
            "id": self.account_id,
            "account_name": self.account_name,
            "exchange": self.exchange,
            "environment": self.environment,
            "credentials": {"apiKey": self.api_key, "secret": self.api_secret},
        }


@router.post("/catalog")
async def market_catalog(tenant_id: str, request: PublicRequest):
    if request.product == "options":
        if request.environment != "production":
            raise ValueError("期权数据仅使用正式公开行情")
        from engine.connectors.binance.options import markets

        return {"items": await asyncio.to_thread(markets)}
    instruments = await queries.instruments(request.environment)
    rows = []
    for instrument in instruments:
        quote = str(instrument.quote_currency)
        symbol = str(instrument.id.symbol).removesuffix("-PERP")
        if (quote not in {"USDT", "USDC"} or str(instrument.settlement_currency) != quote
                or not symbol.endswith(quote)):
            continue
        base = symbol[:-len(quote)]
        rows.append({
            "symbol": f"{base}/{quote}:{instrument.settlement_currency}",
            "market_type": "linear", "base": base, "quote": quote, "expiry": None,
        })
    rows.sort(key=lambda item: item["symbol"])
    return {"items": rows}


@router.post("/tickers")
async def tickers(tenant_id: str, request: PublicRequest):
    return await manual.market_tickers(request.environment)


@router.post("/asset-price")
async def price(tenant_id: str, request: PriceRequest):
    return wire(await asyncio.to_thread(
        assets.asset_price, request.environment, request.currency,
    ))


@router.post("/asset-account")
async def account_assets(tenant_id: str, request: AccountRequest, http_request: Request):
    # A research tenant must never become a tunnel for production credentials.
    tenant = http_request.app.state.runtime.tenant_row(tenant_id)
    if tenant is None or tenant["purpose"] != "production" or tenant["status"] != "active":
        raise HTTPException(403, "仅正式活跃租户可读取真实账户资产。")
    return wire(await __import__("asyncio").to_thread(
        assets.asset_account, request.account(tenant_id), positions=request.positions,
    ))
