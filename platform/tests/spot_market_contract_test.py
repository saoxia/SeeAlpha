"""Spot public data and explicit trading intents have isolated platform boundaries."""

import asyncio
from types import SimpleNamespace

import pytest
from api.engine_client import EngineClient
from api.exchanges import router as exchanges_router
from api.exchanges.trading import SpotCancelRequest, SpotOrderRequest
from fastapi import HTTPException


class MarketVenue:
    def __init__(self):
        self.calls = []

    async def catalog(self, environment, product="futures_um"):
        self.calls.append((environment, product))
        if product == "spot":
            return [
                {"symbol": "BTC/USDT", "market_type": "spot", "base": "BTC",
                 "quote": "USDT", "expiry": None},
                {"symbol": "SOL/USDC", "market_type": "spot", "base": "SOL",
                 "quote": "USDC", "expiry": None},
            ]
        return [{"symbol": "BTC/USDT:USDT", "market_type": "linear",
                 "base": "BTC", "quote": "USDT", "expiry": None}]


class Engine:
    def __init__(self):
        self.calls = []

    async def spot_candles(self, symbol, interval, start_ms, end_ms, *, limit):
        self.calls.append(("candles", symbol, interval, start_ms, end_ms, limit))
        return {"product": "spot", "symbol": symbol, "candles": []}

    async def spot_recent_trades(self, symbol, *, limit):
        self.calls.append(("trades", symbol, limit))
        return {"product": "spot", "symbol": symbol, "trades": [],
                "complete_history": False}


def test_spot_market_catalog_is_not_futures_catalog():
    venue = MarketVenue()
    req = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(exchanges=venue)))
    result = asyncio.run(exchanges_router.markets(
        req, None, exchange="binance", environment="production",
        market_type="spot", search="BTC",
    ))
    assert result[0]["symbol"] == "BTC/USDT"
    assert venue.calls == [("production", "spot")]
    sandbox = asyncio.run(exchanges_router.markets(
        req, None, exchange="binance", environment="sandbox",
        market_type="spot", search="",
    ))
    assert len(sandbox) == 2
    assert venue.calls == [("production", "spot"), ("sandbox", "spot")]


def test_spot_queries_use_read_only_engine_boundary_and_validate_before_dispatch():
    engine = Engine()
    req = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(engine=engine)))
    result = asyncio.run(exchanges_router.spot_candles(
        req, None, symbol="ETH/USDT", interval="1m", start_ms=1000,
        end_ms=61000, limit=100,
    ))
    assert result["symbol"] == "ETHUSDT"
    assert engine.calls == [("candles", "ETHUSDT", "1m", 1000, 61000, 100)]
    trades = asyncio.run(exchanges_router.spot_recent_trades(
        req, None, symbol="ETHUSDC", limit=200,
    ))
    assert trades["complete_history"] is False
    assert engine.calls[-1] == ("trades", "ETHUSDC", 200)
    for symbol, interval, start_ms, end_ms in (
        ("../../etc", "1m", 1, 2),
        ("ETHUSDT", "2m", 1, 2),
        ("ETHUSDT", "1m", 2, 1),
    ):
        with pytest.raises(HTTPException) as error:
            asyncio.run(exchanges_router.spot_candles(
                req, None, symbol=symbol, interval=interval, start_ms=start_ms,
                end_ms=end_ms, limit=100,
            ))
        assert error.value.status_code == 422
    assert len(engine.calls) == 2


def test_engine_client_spot_requests_do_not_include_credentials_or_trading():
    client = EngineClient.__new__(EngineClient)
    calls = []

    async def fake_async(method, path, **kwargs):
        calls.append((method, path, kwargs["json"]))
        return {"product": "spot"}

    client._public_tenant = lambda: "42"
    client._async = fake_async
    asyncio.run(client.spot_candles("BTCUSDT", "1m", 1, 60001, limit=10))
    asyncio.run(client.spot_recent_trades("BTCUSDT", limit=25))
    assert [row[1] for row in calls] == [
        "/tenants/42/venue/spot/klines", "/tenants/42/venue/spot/trades",
    ]
    for method, _, payload in calls:
        assert method == "POST"
        assert payload["environment"] == "production"
        assert "api_key" not in payload and "api_secret" not in payload


def test_spot_orders_reject_perpetual_fields_before_execution():
    from pydantic import ValidationError

    order = {
        "account_revision": "a" * 64,
        "request_id": "123e4567-e89b-42d3-a456-426614174000",
        "symbol": "ETHUSDT",
        "environment": "sandbox",
        "side": "buy",
        "order_type": "limit",
        "quantity": "1.000",
        "price": "2000",
    }
    assert SpotOrderRequest.model_validate(order).symbol == "ETHUSDT"
    for extra in ({"reduce_only": True}, {"position_side": "SHORT"},
                  {"symbol": "ETHUSDT-PERP"}, {"quantity": "-1"}):
        with pytest.raises(ValidationError):
            SpotOrderRequest.model_validate({**order, **extra})
    with pytest.raises(ValidationError):
        SpotOrderRequest.model_validate({**order, "post_only": True, "time_in_force": "IOC"})
    with pytest.raises(ValidationError):
        SpotCancelRequest.model_validate({
            "account_revision": "a" * 64, "request_id": order["request_id"],
            "symbol": "ETHUSDT", "environment": "sandbox",
            "client_order_id": "../../etc/passwd",
        })


def test_spot_client_routes_orders_to_distinct_engine_path():
    client = EngineClient.__new__(EngineClient)
    captured = []

    async def fake_async(method, path, **kwargs):
        captured.append((method, path, kwargs["json"]))
        return {"status": "unknown"}

    client._async = fake_async
    account = {
        "tenant_id": "7", "id": 19, "environment": "sandbox",
        "credentials": {"apiKey": "fake", "secret": "fake"},
    }
    values = {"symbol": "BTCUSDT", "environment": "sandbox", "request_id": "fake"}
    asyncio.run(client.command(account, "submit", values))
    asyncio.run(client.trading_state(account, "BTCUSDT"))
    assert captured[0][1].endswith("/spot/orders")
    assert captured[1][1].endswith("/spot/trading/state")
    assert captured[0][2]["values"]["symbol"] == "BTCUSDT"
