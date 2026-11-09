"""Tenants, empty templates, scoped intent, and archive/restore/delete."""

import json
import tarfile

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import text

from api.auth.security import hash_password
from api.config import Settings
from api.db.auth_repository import auth_repository
from api.db.models import ExchangeAccount, MarketDataPolicy
from api.main import create_app
from tests.api.auth_test import login


@pytest.fixture
def client(tmp_path):
    url = f"sqlite:///{tmp_path / 'business.db'}"
    config = Config("alembic.ini")
    config.attributes["database_url"] = url
    command.upgrade(config, "head")
    app = create_app(Settings(tmp_path / "reference.db", database_url=url, cookie_secure=False))
    app.state.database.initialize()
    auth_repository(app.state.database).create_user("admin", hash_password("test-password-123"))
    with TestClient(app) as test_client:
        login(test_client)
        yield test_client, app


def templates_by_purpose(client):
    rows = client.get("/api/tenants/templates").json()
    return {row["purpose"]: row for row in rows}


def create_tenant(client, purpose, name):
    if purpose == "production":
        rows = client.get("/api/tenants").json()
        return next(row for row in rows if row["purpose"] == "production")
    response = client.post("/api/tenants", json={"name": name})
    assert response.status_code == 201, response.text
    assert response.json()["purpose"] == "development"
    return response.json()


def test_templates_are_empty_and_tenants_start_empty(client):
    http, _app = client
    rows = http.get("/api/tenants/templates").json()
    assert {row["purpose"] for row in rows} == {"production", "development"}
    for row in rows:
        assert set(row) == {"id", "purpose", "name", "description", "created_at"}
        assert row["description"] is None
    created = http.post("/api/tenants/templates", json={
        "purpose": "development", "name": "另一开发模板", "description": "仅说明",
    })
    assert created.status_code == 201, created.text
    assert set(created.json()) == {"id", "purpose", "name", "description", "created_at"}
    for purpose, name in (("production", "正式甲"), ("development", "开发甲")):
        tenant = create_tenant(http, purpose, name)
        assert tenant["purpose"] == purpose and tenant["status"] == "active"
        assert tenant["description"] is None
        assert http.get(f"/api/tenants/{tenant['id']}/accounts").json() == []
        assert http.get(f"/api/tenants/{tenant['id']}/subscriptions").json() == []
        children = _app_children(http, tenant["id"])
        assert children == {"virtual_accounts": 0, "exchange_accounts": 0, "policies": 0}


def _app_children(http, tenant_id):
    return http.app.state.tenants.repository.count_children(tenant_id)


def test_development_rejects_keys_and_live_orders_and_keeps_capital_on_the_account(client, engine_http):
    http, app = client
    tenant = create_tenant(http, "development", "开发资金")
    rejected = http.post(f"/api/tenants/{tenant['id']}/accounts", json={
        "account_name": "should-fail", "exchange": "binance", "environment": "production",
        "api_key": "not-a-real-key", "secret": "not-a-real-secret",
    })
    assert rejected.status_code == 422
    created = http.post(f"/api/tenants/{tenant['id']}/accounts", json={
        "account_name": "paper-cash", "initial_capital": "1000.50", "quote_currency": "USDT",
    })
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["kind"] == "virtual"
    assert body["initial_capital"] == "1000.50" and body["quote_currency"] == "USDT"
    assert "api_key" not in body and "secret" not in body
    denied = engine_http.post(f"/tenants/{tenant['id']}/runnable", json={"action": "live"})
    assert denied.status_code == 409 and "不能向交易所下单" in denied.json()["detail"]
    allowed = engine_http.post(f"/tenants/{tenant['id']}/runnable", json={"action": "backtest"})
    assert allowed.status_code == 200 and allowed.json()["status"] == "runnable"
    denied = engine_http.post(
        f"/tenants/{tenant['id']}/accounts/{body['id']}/connections",
        json={"environment": "production", "api_key": "not-a-real-key",
              "api_secret": "not-a-real-secret"},
    )
    assert denied.status_code == 409 and "不能向交易所下单" in denied.json()["detail"]


def test_production_account_uses_the_key_vault_without_putting_secrets_in_the_book(client):
    http, app = client
    tenant = create_tenant(http, "production", "正式甲")
    rejected = http.post(f"/api/tenants/{tenant['id']}/accounts", json={
        "account_name": "cash", "initial_capital": "10", "quote_currency": "USDT",
    })
    assert rejected.status_code == 422
    created = http.post(f"/api/tenants/{tenant['id']}/accounts", json={
        "account_name": "desk", "exchange": "binance", "environment": "production",
        "api_key": "fixture-key-1234", "secret": "fixture-secret-5678",
    })
    assert created.status_code == 201, created.text
    assert created.json()["kind"] == "exchange"
    assert "fixture-key-1234" not in created.text
    with app.state.database.session() as session:
        row = session.get(ExchangeAccount, created.json()["id"])
        assert row.tenant_id == tenant["id"]
        assert row.credentials_encrypted != "fixture-key-1234"
        assert "fixture-secret-5678" not in row.credentials_encrypted
    blob = b"".join(path.read_bytes() for path in app.state.engine.root.glob("book.sqlite*"))
    assert b"fixture-key-1234" not in blob and b"fixture-secret-5678" not in blob


def test_subscription_intent_is_scoped_by_tenant(client):
    http, app = client
    first = create_tenant(http, "development", "数据甲")
    second = create_tenant(http, "development", "数据乙")
    payload = {
        "symbol": "ETHUSDT", "product": "futures_um", "base_level": "tick",
        "realtime_enabled": False, "history_enabled": True, "history_mode": "start",
        "history_start": "2024-01-01T00:00:00Z",
    }
    saved = http.post(f"/api/tenants/{first['id']}/subscriptions", json=payload)
    assert saved.status_code == 200, saved.text
    other = http.post(f"/api/tenants/{second['id']}/subscriptions", json={**payload, "symbol": "BTCUSDT"})
    assert other.status_code == 200, other.text
    assert [row["symbol"] for row in http.get(f"/api/tenants/{first['id']}/subscriptions").json()] == ["ETHUSDT"]
    assert [row["symbol"] for row in http.get(f"/api/tenants/{second['id']}/subscriptions").json()] == ["BTCUSDT"]
    with app.state.database.session() as session:
        rows = session.query(MarketDataPolicy).all()
        assert {(row.tenant_id, row.symbol) for row in rows} >= {
            (first["id"], "ETHUSDT"), (second["id"], "BTCUSDT"),
        }
    market = app.state.engine.market_account_id
    first_symbols = {row["symbol"] for row in app.state.engine.subscriptions(tenant_id=first["id"], account_id=market)}
    second_symbols = {row["symbol"] for row in app.state.engine.subscriptions(tenant_id=second["id"], account_id=market)}
    assert first_symbols == {"ETHUSDT"} and second_symbols == {"BTCUSDT"}


def test_archive_restore_and_delete_are_distinct(client, engine_http):
    http, app = client
    tenant = create_tenant(http, "development", "可归档")
    http.post(f"/api/tenants/{tenant['id']}/accounts", json={
        "account_name": "cash", "initial_capital": "25", "quote_currency": "USDT",
    })
    http.post(f"/api/tenants/{tenant['id']}/subscriptions", json={
        "symbol": "ETHUSDT", "base_level": "tick", "realtime_enabled": False,
        "history_enabled": True, "history_mode": "start", "history_start": "2024-01-01T00:00:00Z",
    })
    gap_path = f"/tenants/{tenant['id']}/accounts/market-data/gaps"
    assert engine_http.post(gap_path, json={
        "scope": "ETHUSDT", "gap_key": "day", "gap": {"kind": "missing"},
    }).status_code == 200
    archived = http.post(f"/api/tenants/{tenant['id']}/archive")
    assert archived.status_code == 200, archived.text
    assert archived.json()["status"] == "archived"
    assert http.post(f"/api/tenants/{tenant['id']}/archive").status_code == 409
    assert http.post(f"/api/tenants/{tenant['id']}/accounts", json={
        "account_name": "later", "initial_capital": "1", "quote_currency": "USDT",
    }).status_code == 409
    denied = engine_http.post(f"/tenants/{tenant['id']}/runnable", json={"action": "backtest"})
    assert denied.status_code == 409 and "不能交易或回测" in denied.json()["detail"]
    assert engine_http.get(gap_path).json()["gaps"] == []
    path = app.state.tenants._archive_path(tenant["id"])
    assert path.is_file()
    with tarfile.open(path, "r:gz") as archive:
        payload = json.loads(archive.extractfile("tenant.json").read().decode())
    assert payload["business"]["virtual_accounts"][0]["initial_capital"] == "25"
    assert payload["business"]["policies"][0]["symbol"] == "ETHUSDT"
    assert payload["engine"]["tables"]["gaps"]
    assert b"api_key" not in path.read_bytes()
    listed = http.get("/api/tenants").json()
    assert any(row["id"] == tenant["id"] and row["status"] == "archived" for row in listed)
    restored = http.post(f"/api/tenants/{tenant['id']}/restore")
    assert restored.status_code == 200, restored.text
    assert restored.json()["status"] == "active"
    assert engine_http.get(gap_path).json()["gaps"][0]["kind"] == "missing"
    allowed = engine_http.post(f"/tenants/{tenant['id']}/runnable", json={"action": "backtest"})
    assert allowed.status_code == 200 and allowed.json()["status"] == "runnable"
    spare = create_tenant(http, "development", "直接删除")
    assert http.delete(f"/api/tenants/{spare['id']}").status_code == 204
    assert all(row["id"] != spare["id"] for row in http.get("/api/tenants").json())
    assert not app.state.tenants._archive_path(spare["id"]).exists()
    production = next(row for row in http.get("/api/tenants").json() if row["purpose"] == "production")
    assert http.delete(f"/api/tenants/{production['id']}").status_code == 409
    assert http.delete(f"/api/tenants/{tenant['id']}").status_code == 204
    assert not path.exists()
    assert http.get("/api/tenants").json()
    assert all(row["id"] != tenant["id"] for row in http.get("/api/tenants").json())


def test_default_production_tenant_receives_existing_rows(tmp_path):
    url = f"sqlite:///{tmp_path / 'legacy.db'}"
    config = Config("alembic.ini")
    config.attributes["database_url"] = url
    command.upgrade(config, "a9f42e716c08")
    from sqlalchemy import create_engine

    engine = create_engine(url)
    with engine.begin() as connection:
        connection.execute(text(
            """INSERT INTO exchange_accounts (
                   exchange, account_name, environment, credentials_encrypted,
                   market_symbols_json, created_at, updated_at
               ) VALUES ('binance', 'legacy', 'production', 'cipher-text', '[]', 1, 1)"""
        ))
        connection.execute(text(
            """INSERT INTO market_data_policies (product, symbol, parameters)
               VALUES ('futures_um', 'ETHUSDT', '{"symbol":"ETHUSDT","product":"futures_um"}')"""
        ))
    command.upgrade(config, "head")
    app = create_app(Settings(tmp_path / "reference.db", database_url=url, cookie_secure=False))
    app.state.database.initialize()
    auth_repository(app.state.database).create_user("admin", hash_password("test-password-123"))
    with TestClient(app) as http:
        login(http)
        tenants = http.get("/api/tenants").json()
        default = next(row for row in tenants if row["name"] == "默认正式")
        assert default["purpose"] == "production" and default["status"] == "active"
        templates = http.get("/api/tenants/templates").json()
        assert all("symbol" not in row and "initial_capital" not in row for row in templates)
        symbols = [row["symbol"] for row in http.get(f"/api/tenants/{default['id']}/subscriptions").json()]
        assert symbols == ["ETHUSDT"]
        with app.state.database.session() as session:
            account = session.query(ExchangeAccount).one()
            policy = session.query(MarketDataPolicy).one()
            assert account.account_name == "legacy"
            assert account.tenant_id == default["id"] == policy.tenant_id == 1


def test_engine_orders_do_not_cross_tenants(tmp_path):
    from engine.app import create_app as engine_app
    from engine.config import EngineConfig

    app = engine_app(EngineConfig(tmp_path / "state", token="engine-test", project=tmp_path))
    headers = {"Authorization": "Bearer engine-test"}
    order = {
        "environment": "production", "api_key": "key-one", "api_secret": "secret-one",
        "action": "submit", "fingerprint": "a" * 64, "client_order_id": "SA-1",
        "values": {"request_id": "req-1", "symbol": "ETHUSDT-PERP", "account_revision": "f" * 64},
    }

    async def execute(account, symbol, command):
        return {"result": {"client_order_id": command["client_order_id"], "status": "accepted", "symbol": symbol}}

    from engine.connectors.binance import manual
    original = manual.execute
    manual.execute = execute
    try:
        _assert_isolated(app, headers, order)
    finally:
        manual.execute = original


def _assert_isolated(app, headers, order):
    with TestClient(app) as client:
        assert client.put("/tenants/1", headers=headers, json={"purpose": "production"}).status_code == 200
        assert client.put("/tenants/2", headers=headers, json={"purpose": "development"}).status_code == 200
        assert client.post("/tenants/1/accounts/11/orders", headers=headers, json=order).status_code == 200
        blocked = client.post("/tenants/2/accounts/11/orders", headers=headers, json={
            **order, "fingerprint": "b" * 64, "client_order_id": "SA-2",
            "values": {**order["values"], "request_id": "req-2"},
        })
        assert blocked.status_code == 409
        assert client.post("/tenants/2/runnable", headers=headers, json={"action": "backtest"}).status_code == 200
        assert client.post("/tenants/2/runnable", headers=headers, json={"action": "live"}).status_code == 409
        own = client.get("/tenants/1/accounts/11/orders", headers=headers, params={"environment": "production"})
        other = client.get("/tenants/2/accounts/11/orders", headers=headers, params={"environment": "production"})
        assert [row["client_order_id"] for row in own.json()["orders"]] == ["SA-1"]
        assert other.json()["orders"] == []
        blob = b"".join(path.read_bytes() for path in app.state.runtime.store.path.parent.glob("book.sqlite*"))
        assert b"secret-one" not in blob and b"key-one" not in blob


def test_legacy_engine_rows_attach_to_the_default_tenant(tmp_path):
    import sqlite3

    path = tmp_path / "book.sqlite"
    connection = sqlite3.connect(path)
    connection.execute(
        """CREATE TABLE orders (
               account_id TEXT NOT NULL, environment TEXT NOT NULL, client_order_id TEXT NOT NULL,
               order_json TEXT NOT NULL, updated_at INTEGER NOT NULL,
               PRIMARY KEY (account_id, environment, client_order_id))"""
    )
    connection.execute(
        "INSERT INTO orders VALUES ('11', 'production', 'SA-OLD', '{\"client_order_id\":\"SA-OLD\"}', 1)"
    )
    connection.commit()
    connection.close()
    from engine.store import Store

    store = Store(path)
    assert store.orders("1", "11", "production")[0]["client_order_id"] == "SA-OLD"
    assert store.orders("2", "11", "production") == []
    store.close()
