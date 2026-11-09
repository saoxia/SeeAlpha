"""Issued packages on a development tenant backtest catalog history."""

import base64
from datetime import UTC, datetime
from decimal import Decimal

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

from api.auth.security import hash_password
from api.config import Settings
from api.db.auth_repository import auth_repository
from api.main import create_app
from tests.api.auth_test import login
from tests.fixtures.engine_catalog import write_crossover_catalog
from tests.fixtures.strategy_packages import ema_archive


def _client(tmp_path):
    url = f"sqlite:///{tmp_path / 'business.db'}"
    config = Config("alembic.ini")
    config.attributes["database_url"] = url
    command.upgrade(config, "head")
    app = create_app(Settings(tmp_path / "reference.db", database_url=url, cookie_secure=False))
    app.state.database.initialize()
    auth_repository(app.state.database).create_user("admin", hash_password("test-password-123"))
    # The test Engine HTTP service is configured with this isolated project root.
    return app


def _tenant(http, purpose, name):
    if purpose == "production":
        rows = http.get("/api/tenants").json()
        return next(row for row in rows if row["purpose"] == "production")
    templates = {row["purpose"]: row for row in http.get("/api/tenants/templates").json()}
    response = http.post("/api/tenants", json={"template_id": templates[purpose]["id"], "name": name})
    assert response.status_code == 201, response.text
    return response.json()


def test_development_tenant_backtests_an_issued_package(tmp_path):
    start = datetime(2024, 1, 1, tzinfo=UTC)
    write_crossover_catalog(tmp_path / "data" / "catalog", start)
    app = _client(tmp_path)
    window = {"start": "2024-01-01", "end": "2024-01-02", "interval": "1m"}
    with TestClient(app) as http:
        login(http)
        created = http.post("/api/strategy-packages", json={
            "archive": base64.b64encode(ema_archive()).decode(),
            "parameters": {
                "symbol": "ETHUSDT", "fast_period": 3, "slow_period": 5, "allocation": "1",
            },
        })
        assert created.status_code == 201, created.text
        version_id = created.json()["version"]["id"]
        development = _tenant(http, "development", "开发回测")
        production = _tenant(http, "production", "正式回测")
        other = _tenant(http, "development", "另一开发")
        account = http.post(f"/api/tenants/{development['id']}/accounts", json={
            "account_name": "paper", "initial_capital": "1000.50", "quote_currency": "USDT",
        })
        assert account.status_code == 201, account.text
        account_id = account.json()["id"]
        http.post(
            f"/api/strategy-packages/versions/{version_id}/grants",
            json={"purpose": "development"},
        )
        refused = http.post(f"/api/tenants/{production['id']}/strategy-loads", json={
            "account_id": 1, "version_id": version_id,
        })
        assert refused.status_code == 409
        assert "正式授权" in refused.json()["detail"]
        production_run = http.post(
            f"/api/tenants/{production['id']}/accounts/1/backtests", json=window,
        )
        assert production_run.status_code == 409
        assert "开发租户" in production_run.json()["detail"]
        issued = http.post(f"/api/tenants/{development['id']}/strategy-loads", json={
            "account_id": account_id, "version_id": version_id,
        })
        assert issued.status_code == 201, issued.text
        no_intent = http.post(
            f"/api/tenants/{development['id']}/accounts/{account_id}/backtests", json=window,
        )
        assert no_intent.status_code == 409
        assert "订阅" in no_intent.json()["detail"]
        saved = http.post(f"/api/tenants/{development['id']}/subscriptions", json={
            "symbol": "ETHUSDT", "product": "futures_um", "base_level": "tick",
            "realtime_enabled": False, "history_enabled": True, "history_mode": "start",
            "history_start": "2024-01-01T00:00:00Z",
        })
        assert saved.status_code == 200, saved.text
        other_account = http.post(f"/api/tenants/{other['id']}/accounts", json={
            "account_name": "paper", "initial_capital": "10", "quote_currency": "USDT",
        })
        assert other_account.status_code == 201, other_account.text
        other_run = http.post(
            f"/api/tenants/{other['id']}/accounts/{other_account.json()['id']}/backtests",
            json=window,
        )
        assert other_run.status_code == 409
        assert "没有已下发" in other_run.json()["detail"]
        started = http.post(
            f"/api/tenants/{development['id']}/accounts/{account_id}/backtests", json=window,
        )
        assert started.status_code == 200, started.text
        body = started.json()
        assert body["starting_balance"] == "1000.50"
        assert body["quote_currency"] == "USDT"
        assert Decimal(body["ending_balance"]) < Decimal("1000.50")
        assert body["fills"]
        assert body["orders"][0]["tags"] == ["portfolio-net-delta"]
        assert {row["strategy_id"] for row in body["orders"]} == set(body["coordinator_ids"])
        assert set(body["coordinator_ids"]).isdisjoint(body["member_ids"])
        assert "api_key" not in started.text
        latest = http.get(
            f"/api/tenants/{development['id']}/accounts/{account_id}/backtests/latest",
        )
        assert latest.status_code == 200
        assert latest.json() == body
        archived = http.post(f"/api/tenants/{development['id']}/archive")
        assert archived.status_code == 200, archived.text
        blocked = http.post(
            f"/api/tenants/{development['id']}/accounts/{account_id}/backtests", json=window,
        )
        assert blocked.status_code == 409
        assert "归档" in blocked.json()["detail"]
