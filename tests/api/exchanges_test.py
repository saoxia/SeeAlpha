"""API compatibility and credential isolation using deterministic connector snapshots."""

import asyncio
from dataclasses import replace
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from api.auth.security import hash_password
from api.config import Settings
from api.db.auth_repository import AuthRepository
from api.db.database import Database
from api.exchanges.service import summary
from api.main import create_app
from connectors.binance import queries


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
            "market_symbols": ["BTC/USDT:USDT"],
        },
    )


def test_configuration_encryption_auth_and_native_cache(application):
    app, database, calls = application
    with TestClient(app) as client:
        assert client.get("/api/exchange-accounts").status_code == 401
        signin(client, "viewer")
        assert create(client).status_code == 403
        signin(client)
        response = create(client)
        assert response.status_code == 201
        account = response.json()
        assert account["key_hint"] == "••••1234"
        assert "fixture-key" not in response.text and "fixture-secret" not in response.text
        with database.connect() as connection:
            stored = connection.execute(
                "SELECT credentials_encrypted FROM exchange_accounts"
            ).fetchone()[0]
        assert "fixture-key" not in stored and "fixture-secret" not in stored
        account_id = account["id"]
        assert client.get("/api/system/exchange-accounts").json() == [account]
        assert client.get("/api/exchange-accounts").json()[0]["id"] == account_id
        portfolio = client.get(f"/api/exchange-accounts/{account_id}/portfolio")
        assert portfolio.status_code == 200
        assert portfolio.json()["account"]["total_equity"] == "105"
        assert portfolio.json()["positions"][0]["liquidation_price"] is None
        assert (
            client.get("/api/exchange-accounts/portfolio").json()["account"]["total_equity"]
            == "105"
        )
        assert client.get("/api/system/exchange-accounts/balances").json()["items"][0][
            "balances"
        ] == {"USDT": "100"}
        assert len(calls) == 1
        assert client.post(f"/api/system/exchange-accounts/{account_id}/test").status_code == 200
        assert len(calls) == 2
        assert client.delete(f"/api/system/exchange-accounts/{account_id}").status_code == 204
        assert client.get(f"/api/exchange-accounts/{account_id}/portfolio").status_code == 404


def test_keep_credentials_and_reject_incomplete_or_unsupported_changes(application):
    app, _, calls = application
    with TestClient(app) as client:
        signin(client)
        account = create(client).json()
        url = f"/api/system/exchange-accounts/{account['id']}"
        body = {
            "account_name": "renamed",
            "environment": "production",
            "market_symbols": ["ETH/USDT:USDT"],
        }
        assert client.put(url, json=body).status_code == 200
        assert client.get(url + "/portfolio").status_code == 404  # Wrong namespace has no alias.
        assert client.get(f"/api/exchange-accounts/{account['id']}/portfolio").status_code == 200
        assert calls[-1]["credentials"]["secret"] == "fixture-secret-5678"
        assert client.put(url, json={**body, "environment": "sandbox"}).status_code == 422
        assert client.put(url, json={**body, "api_key": "replacement"}).status_code == 422
        assert client.put(url, json={**body, "market_symbols": ["ETH/USDT"]}).status_code == 422
        assert create(client, "renamed").status_code == 409
        assert client.get("/api/system/exchange-markets?market_type=option").status_code == 422


def test_connector_errors_and_validation_never_return_secrets(application, monkeypatch):
    app, _, _ = application

    async def failure(account):
        raise queries.QueryError("Binance 账户查询失败。")

    monkeypatch.setattr(queries, "account_snapshot", failure)
    with TestClient(app) as client:
        signin(client)
        account = create(client).json()
        response = client.get(f"/api/exchange-accounts/{account['id']}/portfolio")
        assert response.status_code == 502
        assert response.headers["cache-control"] == "no-store"
        response = client.post(
            "/api/system/exchange-accounts", json={"api_key": "do-not-echo", "secret": "private"}
        )
        assert response.status_code == 422
        assert "do-not-echo" not in response.text and "private" not in response.text


def test_single_flight_for_concurrent_requests(application):
    app, _, calls = application
    with TestClient(app) as client:
        signin(client)
        account_id = create(client).json()["id"]

    async def scenario():
        service = app.state.exchanges
        await asyncio.gather(*(service.snapshot(account_id) for _ in range(5)))
        assert len(calls) == 1

    asyncio.run(scenario())


def test_equity_invariant_missing_values_and_no_fabricated_zeros():
    result = summary({"total_wallet_balance": "100", "total_unrealized_profit": "5"})
    assert result["total_equity"] == "105"
    assert result["maintenance_margin"] is None and result["effective_leverage"] is None
    assert summary({})["total_equity"] is None
    with pytest.raises(queries.QueryError):
        summary(
            {
                "total_wallet_balance": "100",
                "total_unrealized_profit": "5",
                "total_margin_balance": "100",
            }
        )
    assert Decimal(result["total_equity"]) == Decimal(result["wallet_balance"]) + Decimal(
        result["unrealized_pnl"]
    )


def test_legacy_encrypted_credentials_require_original_key(application):
    app, database, _ = application
    with TestClient(app) as client:
        signin(client)
        create(client)
    key_path = database.path.parent / "credential_key"
    original_key = key_path.read_text()
    key_path.unlink()
    with TestClient(create_app(replace(app.state.settings, credential_key=original_key))) as client:
        signin(client)
        assert client.get("/api/system/exchange-accounts").status_code == 200
    with TestClient(create_app(app.state.settings)) as client:
        signin(client)
        assert client.get("/api/system/exchange-accounts").status_code == 503
        assert not key_path.exists()


def test_sample_equity_candles(application):
    app, _, _ = application
    with TestClient(app) as client:
        path = "/api/exchange-accounts/equity-candles"
        assert client.get(path).status_code == 401
        signin(client)
        account_id = create(client).json()["id"]
        for interval, seconds in [
            ("5m", 300),
            ("15m", 900),
            ("1h", 3600),
            ("4h", 14400),
            ("1d", 86400),
        ]:
            response = client.get(path, params={"interval": interval, "limit": 3})
            assert response.status_code == 200
            result = response.json()
            assert result["is_sample"] is True
            assert result["account_id"] is None
            assert result["interval_seconds"] == seconds
            candles = result["candles"]
            assert len(candles) == 3
            assert candles[1]["bucket_start"] - candles[0]["bucket_start"] == seconds * 1000
            assert all(
                float(c["low"])
                <= min(float(c["open"]), float(c["close"]))
                <= max(float(c["open"]), float(c["close"]))
                <= float(c["high"])
                for c in candles
            )
        result = client.get(f"/api/exchange-accounts/{account_id}/equity-candles").json()
        assert result["account_id"] == account_id and result["is_sample"] is True
        assert client.get("/api/exchange-accounts/999999/equity-candles").status_code == 404
        assert client.get(path, params={"interval": "2m"}).status_code == 422
        assert client.get(path, params={"limit": 501}).status_code == 422
