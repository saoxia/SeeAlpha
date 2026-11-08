"""API compatibility and credential isolation using deterministic connector snapshots."""

import asyncio
import json
from dataclasses import replace
from decimal import Decimal
from functools import partial
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from api.auth.security import hash_password
from api.config import Settings
from api.db.auth_repository import AuthRepository
from api.db.database import Database
from api.exchanges.service import summary
from api.main import create_app
from connectors.binance import assets as asset_queries
from connectors.binance import queries
from connectors.binance.queries import account_snapshot as native_account_snapshot


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
        assert account["market_symbols"] == []
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
        portfolio = client.get(f"/api/exchange-accounts/{account_id}/portfolio?refresh=true")
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
        }
        assert client.put(url, json=body).status_code == 200
        assert client.get(url + "/portfolio").status_code == 404  # Wrong namespace has no alias.
        assert client.get(f"/api/exchange-accounts/{account['id']}/portfolio?refresh=true").status_code == 200
        assert calls[-1]["credentials"]["secret"] == "fixture-secret-5678"
        assert client.put(url, json={**body, "environment": "sandbox"}).status_code == 422
        assert client.put(url, json={**body, "api_key": "replacement"}).status_code == 422
        assert create(client, "renamed").status_code == 409
        assert client.get("/api/system/exchange-markets?market_type=option").status_code == 422


def test_identical_credentials_are_rejected_without_changing_saved_account(application):
    app, _, _ = application
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

        account = create(client).json()
        original = dict(app.state.accounts.row(account["id"]))
        response = client.put(f"/api/system/exchange-accounts/{account['id']}", json=body)
        assert response.status_code == 422
        assert "private-identical-value" not in response.text
        assert dict(app.state.accounts.row(account["id"])) == original
        assert app.state.accounts.private(account["id"])["credentials"] == {
            "version": 1, "apiKey": "fixture-key-1234", "secret": "fixture-secret-5678",
        }


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


def test_historical_symbols_are_preserved_but_do_not_configure_queries(application):
    app, database, calls = application
    with TestClient(app) as client:
        signin(client)
        account = create(client).json()
        account_id = account["id"]
        portfolio_url = f"/api/exchange-accounts/{account_id}/portfolio"
        assert client.get(portfolio_url + "?refresh=true").status_code == 200
        with database.connect() as connection:
            connection.execute(
                "UPDATE exchange_accounts SET market_symbols_json = ? WHERE id = ?",
                ('["ETH/USDT:USDT"]', account_id),
            )
        # Historical watchlist changes neither invalidate snapshots nor filter BTC positions.
        result = client.get(portfolio_url).json()
        assert result["positions"][0]["symbol"] == "BTC/USDT:USDT"
        assert len(calls) == 1
        update = client.put(f"/api/system/exchange-accounts/{account_id}", json={
            "account_name": "renamed", "environment": "production",
            "market_symbols": ["old-client-value-is-ignored"],
        })
        assert update.status_code == 200
        assert update.json()["market_symbols"] == ["ETH/USDT:USDT"]
        with database.connect() as connection:
            assert connection.execute(
                "SELECT market_symbols_json FROM exchange_accounts WHERE id = ?", (account_id,)
            ).fetchone()[0] == '["ETH/USDT:USDT"]'
        assert client.get(portfolio_url + "?refresh=true").status_code == 200
        assert calls[-1]["credentials"]["secret"] == "fixture-secret-5678"
        # Saving an account still invalidates the old snapshot explicitly.
        assert len(calls) == 2
        schemas = client.get("/openapi.json").json()["components"]["schemas"]
        for name in ("ExchangeAccountCreateRequest", "ExchangeAccountUpdateRequest"):
            assert "market_symbols" not in schemas[name]["properties"]
        assert schemas["ExchangeAccountResponse"]["properties"]["market_symbols"]["deprecated"]


def test_connector_errors_and_validation_never_return_secrets(application, monkeypatch):
    app, _, _ = application

    async def failure(account):
        raise queries.QueryError("Binance 账户查询失败。")

    monkeypatch.setattr(queries, "account_snapshot", failure)
    with TestClient(app) as client:
        signin(client)
        account = create(client).json()
        response = client.get(f"/api/exchange-accounts/{account['id']}/portfolio?refresh=true")
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


def test_equity_candles_require_persistent_evidence(application):
    app, _, _ = application
    with TestClient(app) as client:
        path = "/api/exchange-accounts/equity-candles"
        assert client.get(path).status_code == 401
        signin(client)
        account_id = create(client).json()["id"]
        for interval, seconds in [("5m", 300), ("15m", 900), ("1h", 3600), ("4h", 14400), ("1d", 86400)]:
            result = client.get(path, params={"interval": interval, "limit": 3}).json()
            assert result["is_sample"] is False
            assert result["interval_seconds"] == seconds
            assert result["candles"] == [] and result["status"] == "unavailable"
        result = client.get(f"/api/exchange-accounts/{account_id}/equity-candles").json()
        assert result["account_id"] == account_id and result["is_sample"] is False
        assert client.get("/api/exchange-accounts/999999/equity-candles").status_code == 404
        assert client.get(path, params={"interval": "2m"}).status_code == 422
        assert client.get(path, params={"limit": 501}).status_code == 422


@pytest.fixture
def mixed_assets(application, monkeypatch):
    app, _, _ = application
    original = queries.account_snapshot
    clock = [100.0]
    monkeypatch.setattr("api.exchanges.service.time", SimpleNamespace(monotonic=lambda: clock[0], time=lambda: 1785542400 + int(clock[0])))

    async def snapshot(account):
        result = await original(account)
        result.balances["BNB"] = "0.001"
        return result

    monkeypatch.setattr(queries, "account_snapshot", snapshot)
    evidence = {
        "multi_assets": False, "observed_at": 1785542400,
        "totals": {"total_wallet_balance": "101", "total_margin_balance": "106",
                   "total_unrealized_profit": "5", "total_maint_margin": "1"},
        "assets": {
            "USDT": {"walletBalance": Decimal(100), "unrealizedProfit": Decimal(5), "maintMargin": Decimal(1)},
            "BNB": {"walletBalance": Decimal("0.001"), "unrealizedProfit": Decimal(0), "maintMargin": Decimal(0)},
        },
    }
    monkeypatch.setattr(asset_queries, "asset_account", lambda _: evidence)
    prices, price_calls = {"USDT": Decimal(1), "BNB": Decimal(1000)}, []

    def price(env, currency):
        price_calls.append((env, currency))
        if prices[currency] is None:
            raise queries.QueryError("资产指数价格读取失败或已过期。")
        return {"price": prices[currency], "priced_at": 1785542400}

    monkeypatch.setattr(asset_queries, "asset_price", price)
    return app, evidence, prices, price_calls, clock


def test_dust_is_included_and_cached_with_balance_change_and_manual_refresh(mixed_assets):
    app, evidence, _, price_calls, clock = mixed_assets
    with TestClient(app) as client:
        signin(client)
        account_id = create(client, "lly").json()["id"]
        url = f"/api/exchange-accounts/{account_id}/portfolio"
        collect(client, account_id)
        result = client.get(url).json()
        assert result["errors"] == {}
        assert result["account"]["wallet_balance"] == "101.000"
        assert Decimal(result["account"]["total_equity"]) == 106
        assert result["account"]["margin_ratio"] is None
        assert result["valuation_source"] == "estimate"
        bnb = next(a for a in result["assets"] if a["currency"] == "BNB")
        assert bnb["small"] and Decimal(bnb["value_usdt"]) == 1
        assert bnb["account_name"] == "lly"
        clock[0] += 61
        collect(client, account_id)
        client.get(url)
        assert price_calls.count(("production", "BNB")) == 1
        evidence["assets"]["BNB"]["walletBalance"] = Decimal("0.005")
        clock[0] += 61
        collect(client, account_id)
        result = client.get(url).json()
        bnb = next(a for a in result["assets"] if a["currency"] == "BNB")
        assert not bnb["small"] and Decimal(bnb["value_usdt"]) == 5
        assert price_calls.count(("production", "BNB")) == 2
        client.get(url + "?refresh=true")
        assert price_calls.count(("production", "BNB")) == 3
        clock[0] += 61
        collect(client, account_id)
        client.get(url)
        assert price_calls.count(("production", "BNB")) == 4
        assert len(client.get("/api/exchange-accounts/portfolio").json()["assets"]) == 2


def test_missing_prices_keep_old_evidence_and_recover_without_zero_values(mixed_assets):
    app, _, prices, _, clock = mixed_assets
    with TestClient(app) as client:
        signin(client)
        account_id = create(client).json()["id"]
        url = f"/api/exchange-accounts/{account_id}/portfolio"
        collect(client, account_id)
        client.get(url)
        prices["BNB"] = None
        result = client.get(url + "?refresh=true").json()
        assert result["account"]["total_equity"] is None
        assert "暂不可用" in result["errors"]["account"]
        bnb = next(a for a in result["assets"] if a["currency"] == "BNB")
        assert bnb["stale"] and Decimal(bnb["value_usdt"]) == 1
        assert bnb["priced_at"] == 1785542400
        assert "暂不可用" in client.get("/api/exchange-accounts/portfolio").json()["errors"]["account"]
        prices["BNB"] = Decimal(2000)
        clock[0] += 61
        collect(client, account_id)
        result = client.get(url).json()
        assert result["errors"] == {}
        assert Decimal(result["account"]["total_equity"]) == 107


def test_multi_asset_totals_are_not_double_counted_and_stablecoins_use_real_indices(mixed_assets):
    app, evidence, prices, _, _ = mixed_assets
    evidence["multi_assets"] = True
    prices["USDT"] = Decimal("0.5")
    prices["BNB"] = None
    with TestClient(app) as client:
        signin(client)
        account_id = create(client).json()["id"]
        collect(client, account_id)
        result = client.get(f"/api/exchange-accounts/{account_id}/portfolio").json()
        assert result["errors"] == {}
        assert result["valuation_source"] == "exchange"
        assert Decimal(result["account"]["total_equity"]) == 212
        assert next(a for a in result["assets"] if a["currency"] == "BNB")["value_usdt"] is None
        assert "暂未估值" in result["warnings"]["account"]


def test_dust_expiry_does_not_slide_and_negative_assets_are_preserved(mixed_assets):
    app, evidence, prices, calls, clock = mixed_assets
    evidence["assets"]["BNB"]["walletBalance"] = Decimal("-0.001")
    with TestClient(app) as client:
        signin(client)
        account_id = create(client).json()["id"]
        url = f"/api/exchange-accounts/{account_id}/portfolio"
        collect(client, account_id)
        result = client.get(url).json()
        assert Decimal(result["account"]["total_equity"]) == 104
        for instant in (1000, 2000, 3699):
            clock[0] = instant
            collect(client, account_id)
            client.get(url)
        assert calls.count(("production", "BNB")) == 1
        clock[0] = 3760  # First minute collection after the fixed one-hour price expiry.
        prices["BNB"] = Decimal(2000)
        collect(client, account_id)
        result = client.get(url).json()
        assert calls.count(("production", "BNB")) == 2
        assert Decimal(result["account"]["total_equity"]) == 103


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
    service = ExchangeService(None)

    async def check():
        first = await service.catalog("production")
        assert await service.catalog("production") == first
        return first

    items = asyncio.run(check())
    assert [(item["symbol"], item["base"]) for item in items] == [
        ("1000SHIB/USDT:USDT", "1000SHIB"), ("BTC/USDC:USDC", "BTC"), ("XAU/USDT:USDT", "XAU")]
    assert calls == ["production"]


def test_portfolio_reads_only_persisted_data_and_survives_restart(application, monkeypatch):
    app, _, calls = application
    with TestClient(app) as client:
        signin(client)
        account_id = create(client).json()["id"]
        url = f"/api/exchange-accounts/{account_id}/portfolio"
        pending = client.get(url).json()
        assert pending["account"]["total_equity"] is None
        assert "等待后台" in pending["errors"]["account"]
        assert calls == []
        collect(client, account_id)
        result = client.get(url).json()
        assert result["account"]["total_equity"] == "105"
        assert client.get("/api/exchange-accounts/portfolio").json()["account"]["total_equity"] == "105"
        collect(client, account_id)  # Same minute: no duplicate collection.
        assert len(calls) == 1
        path = app.state.exchanges.portfolio_root / f"{account_id}.json"
        content = path.read_text()
        assert "fixture-key" not in content and "fixture-secret" not in content
        assert path.stat().st_mode & 0o077 == 0

    async def unavailable(_):
        raise queries.QueryError("Binance 账户查询失败。")

    monkeypatch.setattr(queries, "account_snapshot", unavailable)
    with TestClient(create_app(app.state.settings)) as client:
        signin(client)
        assert client.get(url).json() == result
        assert client.get(url + "?refresh=true").status_code == 502
        assert path.read_text() == content
        assert client.get(url).json() == result
        saved = json.loads(content)
        saved["saved_at"] -= 121
        path.write_text(json.dumps(saved))
        stale = client.get(url).json()
        assert stale["checked_at"] == result["checked_at"]
        assert stale["account"] == result["account"]
        assert "已过期" in stale["warnings"]["account"]
        assert all(asset["stale"] for asset in stale["assets"])
        assert "已过期" in client.get("/api/exchange-accounts/portfolio").json()["warnings"]["account"]


def test_persisted_portfolio_rejects_old_configuration_and_corrupt_files(application):
    app, _, calls = application
    with TestClient(app) as client:
        signin(client)
        account_id = create(client).json()["id"]
        url = f"/api/exchange-accounts/{account_id}/portfolio"
        collect(client, account_id)
        assert client.put(f"/api/system/exchange-accounts/{account_id}", json={
            "account_name": "renamed", "environment": "production",
        }).status_code == 200
        assert client.get(url).json()["account"]["total_equity"] is None
        collect(client, account_id)
        assert client.get(url).json()["assets"][0]["account_name"] == "renamed"
        assert len(calls) == 2
        path = app.state.exchanges.portfolio_root / f"{account_id}.json"
        path.write_text('{"interrupted":')
        assert client.get(url).json()["account"]["total_equity"] is None
        collect(client, account_id)
        assert client.get(url).json()["account"]["total_equity"] == "105"
        assert client.delete(f"/api/system/exchange-accounts/{account_id}").status_code == 204
        assert client.get(url).status_code == 404
        assert client.get("/api/exchange-accounts/portfolio").json()["assets"] == []


def test_portfolio_collectors_share_writer_lock(application):
    from api.exchanges.service import ExchangeService

    app, _, calls = application
    with TestClient(app) as client:
        signin(client)
        account_id = create(client).json()["id"]

    async def scenario():
        first = app.state.exchanges
        second = ExchangeService(app.state.accounts, first.portfolio_root)
        await asyncio.gather(first.persist_portfolio(account_id), second.persist_portfolio(account_id))
        assert (await second.persisted_portfolio(account_id))["account"]["total_equity"] == "105"
        assert len(calls) == 1

    asyncio.run(scenario())


def test_portfolio_worker_runs_each_minute_and_isolates_account_failures(application, monkeypatch):
    app, _, _ = application
    service = app.state.exchanges
    monkeypatch.setattr(service.accounts, "navigation", lambda: [{"id": 1}, {"id": 2}])
    attempted, intervals = [], []

    async def persist(account_id):
        attempted.append(account_id)
        if account_id == 1:
            raise queries.QueryError("账户读取失败。")

    async def sleep(seconds):
        intervals.append(seconds)
        if len(intervals) == 2:
            raise asyncio.CancelledError

    monkeypatch.setattr(service, "persist_portfolio", persist)
    monkeypatch.setattr("api.exchanges.service.asyncio.sleep", sleep)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(service.portfolio_worker())
    assert attempted == [1, 2, 1, 2]
    assert all(59 < seconds <= 60 for seconds in intervals)
