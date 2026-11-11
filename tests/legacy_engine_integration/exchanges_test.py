"""API compatibility and credential isolation using deterministic connector snapshots."""

import json
from functools import partial

import pytest
from fastapi.testclient import TestClient

from api.auth.security import hash_password
from api.config import Settings
from api.db.auth_repository import AuthRepository
from api.db.database import Database
from api.main import create_app
from engine.connectors.binance import queries
from engine.connectors.binance.queries import account_snapshot as native_account_snapshot

# Legacy app-lifespan regressions opt in to the in-process Engine fixture.
pytestmark = pytest.mark.usefixtures("engine_http")

@pytest.fixture
def application(tmp_path, monkeypatch):
    settings = Settings(tmp_path / "api.db", cookie_secure=False)
    database = Database(settings.database_path)
    database.initialize()
    repository = AuthRepository(database)
    for username in ("admin", "viewer"):
        repository.create_user(username, hash_password("test-password-123"))
    calls = []

    async def snapshot(account):
        calls.append(account)
        return queries.AccountSnapshot(
            {"USDT": "100"},
            {
                "total_wallet_balance": "100",
                "total_margin_balance": "105",
                "total_unrealized_profit": "5",
                "total_maint_margin": "1",
            },
            [
                {
                    "id": "leg-long",
                    "symbol": "BTC/USDT:USDT",
                    "side": "long",
                    "contracts": "0.01",
                    "entry_price": "50000",
                }
            ],
            1785542400,
        )

    monkeypatch.setattr(queries, "account_snapshot", snapshot)
    return create_app(settings), database, calls


def signin(client, username="admin"):
    assert (
        client.post(
            "/api/auth/login",
            json={
                "username": username,
                "password": "test-password-123",
            },
        ).status_code
        == 200
    )


def create(client, name="main"):
    return client.post(
        "/api/system/exchange-accounts",
        json={
            "exchange": "binance",
            "environment": "production",
            "account_name": name,
            "api_key": "fixture-key-1234",
            "secret": "fixture-secret-5678",
        },
    )


def collect(client, account_id):
    """Run one background collection without bypassing the existing price cache."""
    client.portal.call(partial(client.app.state.exchanges.persist_portfolio, account_id))


def test_testing_historical_identical_credentials_returns_actionable_error(application, monkeypatch):
    app, database, _ = application
    monkeypatch.setattr(queries, "account_snapshot", native_account_snapshot)
    with TestClient(app) as client:
        signin(client)
        account = create(client).json()
        encrypted = app.state.accounts.cipher().encrypt(json.dumps({
            "version": 1, "apiKey": "private-identical-value", "secret": "private-identical-value",
        }).encode()).decode()
        with database.connect() as connection:
            connection.execute(
                "UPDATE exchange_accounts SET credentials_encrypted = ? WHERE id = ?",
                (encrypted, account["id"]),
            )
        response = client.post(f"/api/system/exchange-accounts/{account['id']}/test")
        assert response.status_code == 502
        assert "API Key 与 Secret Key 相同" in response.json()["detail"]
        assert "请编辑账户" in response.json()["detail"]
        assert "private-identical-value" not in response.text
        row = app.state.accounts.row(account["id"])
        assert row["last_test_status"] == "failed"
        assert row["credentials_encrypted"] == encrypted


def test_market_catalog_uses_native_contract_names_and_caches(monkeypatch):
    import asyncio
    from types import SimpleNamespace

    from api.exchanges.service import ExchangeService

    calls = []

    async def instruments(env):
        calls.append(env)
        return [SimpleNamespace(id=SimpleNamespace(symbol=symbol), base_currency=base,
                                quote_currency=quote, settlement_currency=quote)
                for symbol, base, quote in [("XAUUSDT-PERP", None, "USDT"),
                                            ("1000SHIBUSDT-PERP", "SHIB", "USDT"),
                                            ("BTCUSDC-PERP", "BTC", "USDC")]]

    monkeypatch.setattr(queries, "instruments", instruments)

    from engine.public_queries import PublicRequest, market_catalog

    async def catalogue(environment, product):
        payload = PublicRequest(environment=environment, product=product)
        return (await market_catalog("1", payload))["items"]

    service = ExchangeService(None, engine=SimpleNamespace(market_catalog=catalogue))

    async def check():
        first = await service.catalog("production")
        assert await service.catalog("production") == first
        return first

    items = asyncio.run(check())
    assert [(item["symbol"], item["base"]) for item in items] == [
        ("1000SHIB/USDT:USDT", "1000SHIB"), ("BTC/USDC:USDC", "BTC"), ("XAU/USDT:USDT", "XAU")]
    assert calls == ["production"]


