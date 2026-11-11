"""Engine-independent account and manual-trading HTTP boundary regressions.

The Platform API, session authentication, encrypted business records and
pre-dispatch validation are real. Every Engine HTTP bootstrap operation is
handled by a strict local stub, and unexpected order sends fail this suite.
No Docker, Binance connector, or production account is involved.
"""

from decimal import Decimal
from uuid import uuid4

import pytest
from api.auth.security import hash_password
from api.config import Settings
from api.db.auth_repository import AuthRepository
from api.db.database import Database
from api.exchanges.service import summary
from api.main import create_app
from core.engine_contracts import QueryError
from fastapi.testclient import TestClient


@pytest.fixture
def environment(tmp_path, monkeypatch, platform_engine_bootstrap):
    settings = Settings(tmp_path / "api.db", cookie_secure=False)
    database = Database(settings.database_path)
    database.initialize()
    users = AuthRepository(database)
    for username in ("admin", "viewer"):
        users.create_user(username, hash_password("test-password-123"))
    monkeypatch.setattr("api.exchanges.trading.account_revision", lambda _account: "f" * 64)
    app = create_app(settings)
    dispatched = []

    async def unexpected_order(account, action, values):
        dispatched.append((account["id"], action, values))
        raise AssertionError("Platform must reject this request before Engine order dispatch")

    monkeypatch.setattr(app.state.engine, "command", unexpected_order)
    return app, database, dispatched


def signin(client, username="admin"):
    response = client.post("/api/auth/login", json={
        "username": username, "password": "test-password-123",
    })
    assert response.status_code == 200, response.text


def create_account(client, name="main"):
    response = client.post("/api/system/exchange-accounts", json={
        "exchange": "binance", "environment": "production",
        "account_name": name, "api_key": "fixture-key-1234",
        "secret": "fixture-secret-5678",
    })
    assert response.status_code == 201, response.text
    return response.json()


def order_payload(**values):
    return {
        "account_revision": "f" * 64, "request_id": str(uuid4()),
        "symbol": "ETHUSDT-PERP", "environment": "production",
        "side": "buy", "order_type": "limit", "quantity": "0.01", "price": "2000",
        **values,
    }


def test_identical_credentials_are_rejected_without_changing_saved_account(environment):
    app, _database, dispatched = environment
    with TestClient(app) as client:
        signin(client)
        body = {
            "exchange": "binance", "account_name": "bad-pair", "environment": "production",
            "api_key": "  private-identical-value ", "secret": "private-identical-value",
        }
        response = client.post("/api/system/exchange-accounts", json=body)
        assert response.status_code == 422
        assert "不能相同" in response.json()["detail"]
        assert "private-identical-value" not in response.text
        assert client.get("/api/system/exchange-accounts").json() == []

        account = create_account(client)
        original = dict(app.state.accounts.row(account["id"]))
        response = client.put(f"/api/system/exchange-accounts/{account['id']}", json=body)
        assert response.status_code == 422
        assert "private-identical-value" not in response.text
        assert dict(app.state.accounts.row(account["id"])) == original
        assert app.state.accounts.private(account["id"])["credentials"] == {
            "version": 1, "apiKey": "fixture-key-1234", "secret": "fixture-secret-5678",
        }
    assert dispatched == []


def test_equity_invariant_missing_values_and_no_fabricated_zeros():
    result = summary({"total_wallet_balance": "100", "total_unrealized_profit": "5"})
    assert result["total_equity"] == "105"
    assert result["maintenance_margin"] is None and result["effective_leverage"] is None
    assert summary({})["total_equity"] is None
    with pytest.raises(QueryError):
        summary({
            "total_wallet_balance": "100",
            "total_unrealized_profit": "5",
            "total_margin_balance": "100",
        })
    assert Decimal(result["total_equity"]) == (
        Decimal(result["wallet_balance"]) + Decimal(result["unrealized_pnl"])
    )


def test_equity_candles_require_persistent_evidence(environment):
    app, _database, dispatched = environment
    with TestClient(app) as client:
        path = "/api/exchange-accounts/equity-candles"
        assert client.get(path).status_code == 401
        signin(client)
        account_id = create_account(client)["id"]
        for interval, seconds in (
            ("5m", 300), ("15m", 900), ("1h", 3600), ("4h", 14400), ("1d", 86400)
        ):
            result = client.get(path, params={"interval": interval, "limit": 3}).json()
            assert result["is_sample"] is False
            assert result["interval_seconds"] == seconds
            assert result["candles"] == [] and result["status"] == "unavailable"
        result = client.get(f"/api/exchange-accounts/{account_id}/equity-candles").json()
        assert result["account_id"] == account_id and result["is_sample"] is False
        assert client.get("/api/exchange-accounts/999999/equity-candles").status_code == 404
        assert client.get(path, params={"interval": "2m"}).status_code == 422
        assert client.get(path, params={"limit": 501}).status_code == 422
    assert dispatched == []


@pytest.mark.parametrize("changes", [
    {"quantity": "NaN"}, {"quantity": "0"}, {"quantity": "-1"},
    {"price": None}, {"price": "0"}, {"symbol": "../BTC"},
    {"order_type": "market", "price": "2000"},
    {"post_only": True, "time_in_force": "IOC"},
    {"reduce_only": True, "position_side": "LONG"},
])
def test_invalid_orders_never_reach_engine(environment, changes):
    app, _database, dispatched = environment
    with TestClient(app) as client:
        signin(client)
        create_account(client)
        response = client.post(
            "/api/trading/accounts/1/orders", json=order_payload(**changes)
        )
        assert response.status_code == 422, response.text
    assert dispatched == []


def test_environment_mismatch_and_missing_account_fail_before_send(environment):
    app, _database, dispatched = environment
    with TestClient(app) as client:
        signin(client)
        create_account(client)
        assert client.post(
            "/api/trading/accounts/1/orders",
            json=order_payload(environment="sandbox"),
        ).status_code == 409
        assert client.post(
            "/api/trading/accounts/99/orders", json=order_payload()
        ).status_code == 404
    assert dispatched == []


def test_trade_permissions_origin_and_stale_revision_fail_before_dispatch(environment):
    app, _database, dispatched = environment
    with TestClient(app) as client:
        endpoint = "/api/trading/accounts/1/orders"
        assert client.post(endpoint, json=order_payload()).status_code == 401
        signin(client, "viewer")
        assert client.post(endpoint, json=order_payload()).status_code == 403
        signin(client)
        create_account(client)
        assert client.post(
            endpoint, json=order_payload(),
            headers={"Origin": "https://evil.invalid"},
        ).status_code == 403
        assert client.post(
            endpoint, json=order_payload(account_revision="e" * 64),
        ).status_code == 409
    assert dispatched == []


def test_market_picker_permissions_full_catalog_and_environment(environment, monkeypatch):
    app, _database, dispatched = environment
    calls = []

    async def catalog(environment):
        calls.append(environment)
        return [
            {"base": f"TEST{i}", "quote": "USDT"} for i in range(205)
        ] + [{"base": "ETH", "quote": "USDC"}]

    async def tickers(environment):
        calls.append(environment)
        return {
            "checked_at": 123,
            "rows": {"ETHUSDC": {
                "last_price": "2225.33",
                "change_percent": "-6.76",
                "quote_volume": "3700000000",
                "funding_rate": "-0.0000252",
            }},
        }

    monkeypatch.setattr(app.state.exchanges, "catalog", catalog)
    monkeypatch.setattr(app.state.engine, "market_tickers", tickers)
    with TestClient(app) as client:
        assert client.get("/api/trading/markets").status_code == 401
        signin(client, "viewer")
        assert client.get("/api/trading/markets").status_code == 403
        signin(client)
        response = client.get("/api/trading/markets?environment=sandbox")
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["environment"] == "sandbox" and calls == ["sandbox", "sandbox"]
        assert len(result["markets"]) == 206
        assert result["markets"][0]["last_price"] is None
        assert result["markets"][-1]["symbol"] == "ETHUSDC-PERP"
        assert result["markets"][-1]["funding_rate"] == "-0.0000252"
        assert client.get("/api/trading/markets?environment=invalid").status_code == 422
    assert dispatched == []
