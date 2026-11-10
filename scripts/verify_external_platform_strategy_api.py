"""Full real business API strategy package / preview regression over external Engine.

Migrated from tests/api/strategy_store_test.py. Both original test scenarios
run against actual Platform REST routes and an independently built Engine over
loopback HTTP v2. The only replacement assertion checks the actual exported
remote Engine tenant state rather than a vacuous Platform-local book.sqlite glob.

Run exclusively inside verify_external_engine.sh's disposable multi-tenant
sandbox, with a fresh migrated SQLite DB per case. No exchange credentials,
market downloads or orders are used. No legacy Engine/Nautilus imports.
"""

import base64
import io
import json
import os
import sys
import zipfile
from decimal import Decimal
from pathlib import Path

import httpx
from fastapi.testclient import TestClient


def run_cases(state, engine_http):
    # These standard-library fixtures are explicitly copied into the isolated
    # Platform test tree. Neither the legacy Engine nor its test suite is copied.
    import verify_external_platform_backtest_api as support

    from api.db.models import ExchangeAccount
    from tests.fixtures.strategy_packages import (
        crossover_bars,
        ema_archive,
        hold_archive,
    )

    _client = support._client
    login = support.login

    def _templates(http):
        rows = http.get("/api/tenants/templates").json()
        return {row["purpose"]: row for row in rows}


    def _tenant(http, purpose, name):
        if purpose == "production":
            rows = http.get("/api/tenants").json()
            return next(row for row in rows if row["purpose"] == "production")
        template = _templates(http)[purpose]
        response = http.post("/api/tenants", json={"template_id": template["id"], "name": name})
        assert response.status_code == 201, response.text
        return response.json()


    def _encoded(archive):
        return base64.b64encode(archive).decode()


    def _package(http, parameters=None, version="1"):
        body = {
            "archive": _encoded(ema_archive(version)),
            "parameters": parameters if parameters is not None else {
                "symbol": "ETHUSDT", "fast_period": 3, "slow_period": 5, "allocation": "1",
            },
        }
        if version == "1":
            created = http.post("/api/strategy-packages", json=body)
            assert created.status_code == 201, created.text
            payload = created.json()
            return payload, payload["version"]
        raise AssertionError("additional versions use the version route")


    def test_catalog_grant_and_development_issue_round_trip(tmp_path, engine_http):
        app = _client(tmp_path)
        with TestClient(app) as http:
            login(http)
            invalid = engine_http.post(
                "/tenants/1/strategy-packages/inspect",
                json={"archive": "not-a-zip", "parameters": {}},
            )
            assert invalid.status_code == 422
            rejected = http.post("/api/strategy-packages", json={"archive": "not-a-zip"})
            assert rejected.status_code == 422
            slipped = io.BytesIO()
            with zipfile.ZipFile(slipped, "w") as archive:
                archive.writestr("seealpha-package.json", json.dumps({
                    "format": 1, "name": "imported_tree", "title": "研究包", "version": "1",
                    "entry_point": "strategy:Hold",
                }))
                archive.writestr("strategy.py", "import strategy_python\n")
            research = http.post("/api/strategy-packages", json={"archive": _encoded(slipped.getvalue())})
            assert research.status_code == 422
            assert "strategy_python" in research.json()["detail"]
            package, version = _package(http)
            listed = http.get("/api/strategy-packages").json()
            assert listed[0]["code_key"] == "ema_crossover"
            assert listed[0]["versions"][0]["version"] == "1"
            assert listed[0]["versions"][0]["entry_point"] == "strategy:EmaCrossover"
            assert listed[0]["versions"][0]["package_hash"] == version["package_hash"]
            assert "archive" not in listed[0]["versions"][0]
            assert listed[0]["versions"][0]["grants"] == []
            assert listed[0]["versions"][0]["parameters"]["risk_limit"] == "1"
            duplicate = http.post("/api/strategy-packages", json={"archive": _encoded(ema_archive())})
            assert duplicate.status_code == 409
            bad = http.post(f"/api/strategy-packages/{package['id']}/versions", json={
                "archive": _encoded(ema_archive("2")),
                "parameters": {"fast_period": 21, "slow_period": 8},
            })
            assert bad.status_code == 422
            secret = http.post(f"/api/strategy-packages/{package['id']}/versions", json={
                "archive": _encoded(ema_archive("3")),
                "parameters": {"api_key": "no"},
            })
            assert secret.status_code == 422

            other_archive = hold_archive(tmp_path / "hold")
            other = http.post("/api/strategy-packages", json={"archive": _encoded(other_archive)})
            assert other.status_code == 201, other.text
            assert other.json()["code_key"] == "hold_long"

            development = _tenant(http, "development", "开发甲")
            other_tenant = _tenant(http, "development", "开发乙")
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
            assert body["entry_point"] == "strategy:EmaCrossover"
            assert "api_key" not in issued.text
            assert "archive" not in body
            loads = http.get(f"/api/tenants/{development['id']}/strategy-loads").json()
            assert [row["id"] for row in loads] == [body["id"]]
            assert http.get(f"/api/tenants/{other_tenant['id']}/strategy-loads").json() == []
            engine_rows = engine_http.get(f"/tenants/{development['id']}/strategy-loads").json()["loads"]
            assert len(engine_rows) == 1
            assert engine_rows[0]["grant_purpose"] == "development"
            assert engine_rows[0]["package_hash"] == version["package_hash"]
            assert "archive" not in engine_rows[0]
            assert engine_http.get(f"/tenants/{other_tenant['id']}/strategy-loads").json()["loads"] == []
            preview_response = engine_http.post(
                f"/tenants/{development['id']}/accounts/{account.json()['id']}/strategy-preview",
                json={"bars": crossover_bars()},
            )
            assert preview_response.status_code == 200, preview_response.text
            preview = preview_response.json()
            assert [Decimal(row["quantity"]) for row in preview["holdings"]] == [Decimal(1), Decimal(0)]
            assert [row["side"] for row in preview["orders"]] == ["BUY", "SELL"]
            assert all(row["tags"] == ["portfolio-net-delta"] for row in preview["orders"])
            assert {row["strategy_id"] for row in preview["orders"]} == set(preview["coordinator_ids"])
            assert set(preview["coordinator_ids"]).isdisjoint(preview["member_ids"])


    def test_production_load_needs_its_own_grant(tmp_path, engine_http):
        app = _client(tmp_path)
        with TestClient(app) as http:
            login(http)
            package, version = _package(http)
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
            rows = engine_http.get(
                f"/tenants/{production['id']}/strategy-loads", params={"account_id": str(account_id)},
            ).json()["loads"]
            assert rows[0]["grant_purpose"] == "production"
            assert rows[0]["code_key"] == "ema_crossover"
            # The resident Engine has its own storage. Its exported tenant state
            # must never include the encrypted credentials kept in Platform ORM.
            exported = engine_http.get(f"/tenants/{production['id']}/export")
            assert exported.status_code == 200, exported.text
            assert "unused" not in json.dumps(exported.json(), ensure_ascii=False)
            dev_account = http.post(f"/api/tenants/{development['id']}/accounts", json={
                "account_name": "paper", "initial_capital": "10", "quote_currency": "USDT",
            })
            second = http.post("/api/strategy-packages", json={"archive": _encoded(ema_archive())})
            assert second.status_code == 409
            only_production = http.post(
                f"/api/strategy-packages/{package['id']}/versions",
                json={"archive": _encoded(ema_archive("prod-only"))},
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

    # Each case starts from an independent business database. The same Engine
    # container is reused, but its known disposable test tenants are cleared
    # between cases so strategy grants/loads cannot leak into the next case.
    first = state / "catalog-case"
    first.mkdir(mode=0o700)
    test_catalog_grant_and_development_issue_round_trip(first, engine_http)

    def reset_disposable_tenants():
        for tenant_id in (1, 2, 3):
            response = engine_http.post(f"/tenants/{tenant_id}/drop", params={"remove": True})
            assert response.status_code == 200, response.text
            assert response.json()["status"] == "deleted"

    reset_disposable_tenants()
    second = state / "production-case"
    second.mkdir(mode=0o700)
    test_production_load_needs_its_own_grant(second, engine_http)
    reset_disposable_tenants()


def main():
    import verify_external_platform_backtest_api as support

    root, state, _package = support._environment()
    # Clean environment + loopback/token guards are also enforced by the parent
    # runner and shared business API smoke. Refuse host proxy/secret inheritance.
    for name in ("DATABASE_URL", "DATABASE_URL_FILE", "SYSTEM_CREDENTIAL_KEY",
                 "ETL_CREDENTIAL_KEY", "BINANCE_API_KEY", "BINANCE_API_SECRET"):
        if name in os.environ:
            raise RuntimeError(f"Unexpected host database/credential variable: {name}")
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
                 "http_proxy", "https_proxy", "all_proxy"):
        os.environ.pop(name, None)
    os.environ["NO_PROXY"] = "127.0.0.1,localhost"
    os.environ["no_proxy"] = "127.0.0.1,localhost"

    sys.path.insert(0, str(root))
    import api
    import core
    from core.engine_contracts import ENGINE_CONTRACT_HEADER, ENGINE_CONTRACT_VERSION

    assert Path(api.__file__).resolve().is_relative_to(root)
    assert Path(core.__file__).resolve().is_relative_to(root)
    assert "engine" not in sys.modules and "nautilus_trader" not in sys.modules
    support._wait_http_engine()
    with httpx.Client(
        base_url=os.environ["ENGINE_URL"].rstrip("/"), timeout=60,
        headers={
            "Authorization": f'Bearer {os.environ["ENGINE_TOKEN"]}',
            ENGINE_CONTRACT_HEADER: ENGINE_CONTRACT_VERSION,
        },
        trust_env=False,
    ) as engine_http:
        run_cases(state, engine_http)
    assert "engine" not in sys.modules and "nautilus_trader" not in sys.modules
    print("PASS: migrated real Platform strategy catalog/grants/issuance/preview; "
          "cross-tenant and production permissions via independent Engine HTTP")


if __name__ == "__main__":
    # Python -I does not automatically add the containing scripts/ directory.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    main()
