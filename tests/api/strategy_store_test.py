"""Strategy catalog, tenant-type grants, and issuance onto a development tenant."""

from decimal import Decimal

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

from api.auth.security import hash_password
from api.config import Settings
from api.db.auth_repository import auth_repository
from api.db.models import ExchangeAccount
from api.main import create_app
from tests.api.auth_test import login
from tests.engine.strategy_load_test import crossover_bars


def _client(tmp_path):
    url = f"sqlite:///{tmp_path / 'business.db'}"
    config = Config("alembic.ini")
    config.attributes["database_url"] = url
    command.upgrade(config, "head")
    app = create_app(Settings(tmp_path / "reference.db", database_url=url, cookie_secure=False))
    app.state.database.initialize()
    auth_repository(app.state.database).create_user("admin", hash_password("test-password-123"))
    return app


def _templates(http):
    rows = http.get("/api/tenants/templates").json()
    return {row["purpose"]: row for row in rows}


def _tenant(http, purpose, name):
    template = _templates(http)[purpose]
    response = http.post("/api/tenants", json={"template_id": template["id"], "name": name})
    assert response.status_code == 201, response.text
    return response.json()


def _package(http, name="双均线"):
    created = http.post("/api/strategy-packages", json={
        "code_key": "ema_crossover", "name": name, "description": "金叉满额，死叉空仓",
    })
    assert created.status_code == 201, created.text
    version = http.post(f"/api/strategy-packages/{created.json()['id']}/versions", json={
        "version": "1",
        "parameters": {"symbol": "ETHUSDT", "fast_period": 3, "slow_period": 5, "allocation": "1"},
    })
    assert version.status_code == 201, version.text
    return created.json(), version.json()


def test_catalog_grant_and_development_issue_round_trip(tmp_path):
    app = _client(tmp_path)
    with TestClient(app) as http:
        login(http)
        rejected = http.post("/api/strategy-packages", json={
            "code_key": "strategy_python_btc", "name": "研究包",
        })
        assert rejected.status_code == 422
        unknown = http.post("/api/strategy-packages", json={"code_key": "not_registered", "name": "未登记"})
        assert unknown.status_code == 422
        package, version = _package(http)
        listed = http.get("/api/strategy-packages").json()
        assert listed[0]["code_key"] == "ema_crossover"
        assert listed[0]["versions"][0]["version"] == "1"
        assert listed[0]["versions"][0]["grants"] == []
        assert listed[0]["versions"][0]["parameters"]["risk_limit"] == "1"
        duplicate = http.post("/api/strategy-packages", json={"code_key": "ema_crossover", "name": "另一个名字"})
        assert duplicate.status_code == 409
        bad = http.post(f"/api/strategy-packages/{package['id']}/versions", json={
            "version": "2", "parameters": {"fast_period": 21, "slow_period": 8},
        })
        assert bad.status_code == 422
        secret = http.post(f"/api/strategy-packages/{package['id']}/versions", json={
            "version": "3", "parameters": {"api_key": "no"},
        })
        assert secret.status_code == 422

        development = _tenant(http, "development", "开发甲")
        other = _tenant(http, "development", "开发乙")
        account = http.post(f"/api/tenants/{development['id']}/accounts", json={
            "account_name": "paper", "initial_capital": "1000", "quote_currency": "USDT",
        })
        assert account.status_code == 201, account.text
        blocked = http.post(f"/api/tenants/{development['id']}/strategy-loads", json={
            "account_id": account.json()["id"], "version_id": version["id"],
        })
        assert blocked.status_code == 409
        grant = http.post(
            f"/api/strategy-packages/versions/{version['id']}/grants",
            json={"purpose": "development"},
        )
        assert grant.status_code == 201, grant.text
        again = http.post(
            f"/api/strategy-packages/versions/{version['id']}/grants",
            json={"purpose": "development"},
        )
        assert again.status_code == 201
        assert again.json()["id"] == grant.json()["id"]
        issued = http.post(f"/api/tenants/{development['id']}/strategy-loads", json={
            "account_id": account.json()["id"], "version_id": version["id"],
        })
        assert issued.status_code == 201, issued.text
        body = issued.json()
        assert body["status"] == "loaded"
        assert body["purpose"] == "development"
        assert body["code_key"] == "ema_crossover"
        assert "api_key" not in issued.text
        loads = http.get(f"/api/tenants/{development['id']}/strategy-loads").json()
        assert [row["id"] for row in loads] == [body["id"]]
        assert http.get(f"/api/tenants/{other['id']}/strategy-loads").json() == []
        engine_rows = app.state.engine.runtime.store.strategy_loads(development["id"])
        assert len(engine_rows) == 1
        assert engine_rows[0]["grant_purpose"] == "development"
        assert app.state.engine.runtime.store.strategy_loads(other["id"]) == []
        preview = app.state.engine.runtime.preview_strategy(
            development["id"], account.json()["id"], crossover_bars(),
        )
        assert [Decimal(row["quantity"]) for row in preview["holdings"]] == [Decimal(1), Decimal(0)]
        assert [row["side"] for row in preview["orders"]] == ["BUY", "SELL"]
        assert all(row["tags"] == ["portfolio-net-delta"] for row in preview["orders"])
        assert {row["strategy_id"] for row in preview["orders"]} == set(preview["coordinator_ids"])
        assert set(preview["coordinator_ids"]).isdisjoint(preview["member_ids"])


def test_production_load_needs_its_own_grant(tmp_path):
    app = _client(tmp_path)
    with TestClient(app) as http:
        login(http)
        _package_row, version = _package(http, "正式候选")
        http.post(
            f"/api/strategy-packages/versions/{version['id']}/grants",
            json={"purpose": "development"},
        )
        production = _tenant(http, "production", "正式甲")
        development = _tenant(http, "development", "开发甲")
        refused = http.post(f"/api/tenants/{production['id']}/strategy-loads", json={
            "account_id": "1", "version_id": version["id"],
        })
        assert refused.status_code == 409
        assert "正式授权" in refused.json()["detail"]
        granted = http.post(
            f"/api/strategy-packages/versions/{version['id']}/grants",
            json={"purpose": "production"},
        )
        assert granted.status_code == 201, granted.text
        missing = http.post(f"/api/tenants/{production['id']}/strategy-loads", json={
            "account_id": "999", "version_id": version["id"],
        })
        assert missing.status_code == 404
        with app.state.database.session(write=True) as session:
            account = ExchangeAccount(
                tenant_id=production["id"], exchange="binance", account_name="desk",
                environment="production", credentials_encrypted="unused",
                market_symbols_json="[]", created_at=1, updated_at=1,
            )
            session.add(account)
            session.flush()
            account_id = account.id
        issued = http.post(f"/api/tenants/{production['id']}/strategy-loads", json={
            "account_id": account_id, "version_id": version["id"],
        })
        assert issued.status_code == 201, issued.text
        assert issued.json()["purpose"] == "production"
        assert "unused" not in issued.text
        rows = app.state.engine.runtime.store.strategy_loads(production["id"], str(account_id))
        assert rows[0]["grant_purpose"] == "production"
        assert rows[0]["code_key"] == "ema_crossover"
        blob = b"".join(path.read_bytes() for path in app.state.engine.root.glob("book.sqlite*"))
        assert b"unused" not in blob
        dev_account = http.post(f"/api/tenants/{development['id']}/accounts", json={
            "account_name": "paper", "initial_capital": "10", "quote_currency": "USDT",
        })
        second = http.post("/api/strategy-packages", json={"code_key": "ema_crossover", "name": "仅正式"})
        assert second.status_code == 409
        only_production = http.post(
            f"/api/strategy-packages/{_package_row['id']}/versions",
            json={"version": "prod-only"},
        )
        assert only_production.status_code == 201, only_production.text
        http.post(
            f"/api/strategy-packages/versions/{only_production.json()['id']}/grants",
            json={"purpose": "production"},
        )
        dev_blocked = http.post(f"/api/tenants/{development['id']}/strategy-loads", json={
            "account_id": dev_account.json()["id"], "version_id": only_production.json()["id"],
        })
        assert dev_blocked.status_code == 409
        assert "开发" in dev_blocked.json()["detail"]
