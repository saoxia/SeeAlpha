"""Real, isolated Platform /api -> independent Engine historical-backtest regression.

This is the former tests/api/backtest_test.py, with its real BFF assertions
preserved. It runs only under the disposable integration runner, with a new
multi-tenant Engine container and a freshly migrated business SQLite database.
There are no mocks for the routes, Engine HTTP, or Nautilus backtest. The API
lifespan is disabled solely to prevent unsolicited background market workers.
"""

import base64
import io
import ipaddress
import os
import sys
import zipfile
from contextlib import asynccontextmanager
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlsplit
from time import monotonic, sleep

import httpx
from fastapi.testclient import TestClient


def _environment():
    if os.environ.get("SEEALPHA_SMOKE_DISPOSABLE") != "1":
        raise RuntimeError("Only disposable external Engine integration is permitted")
    root = Path(os.environ["SEEALPHA_ISOLATED_PLATFORM_ROOT"]).resolve()
    state = Path(os.environ["SEEALPHA_API_TEST_STATE_DIR"]).resolve()
    if state.parent != root.parent or (state / "business.db").exists():
        raise RuntimeError("API database must be fresh and in the disposable sandbox")
    if not (root / "api").is_dir() or not (root / "core").is_dir():
        raise RuntimeError("Missing isolated Platform source tree")
    if (root / "engine").exists() or (root / "nautilus_support.py").exists():
        raise RuntimeError("Legacy Engine must not appear in Platform source tree")
    if not (root / "migrations").is_dir() or not (root / "alembic.ini").is_file():
        raise RuntimeError("Missing actual Platform Alembic migrations")
    package = root / "packages" / "ema_crossover"
    if not (package / "seealpha-package.json").is_file():
        raise RuntimeError("Missing portable EMA strategy example")
    url = os.environ["ENGINE_URL"].rstrip("/")
    parsed = urlsplit(url)
    if (parsed.scheme != "http" or not parsed.hostname
            or not ipaddress.ip_address(parsed.hostname).is_loopback
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.path not in ("", "/")):
        raise RuntimeError("External Engine API smoke must stay on loopback")
    token = os.environ["ENGINE_TOKEN"]
    if len(token) < 40 or token == "seealpha-local-engine":
        raise RuntimeError("Disposable private Engine token required")
    return root, state, package


def _zip_strategy_directory(root):
    """Pure-stdlib deterministic strategy fixture, no Nautilus/Engine imports."""
    result = io.BytesIO()
    with zipfile.ZipFile(result, "w") as archive:
        for path in sorted(p for p in root.rglob("*") if p.is_file()
                           and "__pycache__" not in p.parts):
            relative = path.relative_to(root).as_posix()
            if path.suffix.lower() not in {".py", ".json", ".md", ".txt"}:
                continue
            member = zipfile.ZipInfo(relative)
            member.date_time = (2026, 1, 1, 0, 0, 0)
            member.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(member, path.read_bytes())
    return result.getvalue()


@asynccontextmanager
async def _isolated_http_only_lifespan(_app):
    """No periodic market/equity tasks; real API routing stays untouched."""
    yield


def _client(tmp_path):
    from alembic import command
    from alembic.config import Config
    from api.auth.security import hash_password
    from api.config import Settings
    from api.db.auth_repository import auth_repository
    from api.main import create_app

    root = Path(os.environ["SEEALPHA_ISOLATED_PLATFORM_ROOT"]).resolve()
    url = f"sqlite:///{tmp_path / 'business.db'}"
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "migrations"))
    config.attributes["database_url"] = url
    command.upgrade(config, "head")
    app = create_app(Settings(
        tmp_path / "reference.db", database_url=url, cookie_secure=False,
        engine_url=os.environ["ENGINE_URL"], engine_token=os.environ["ENGINE_TOKEN"],
    ))
    app.state.database.initialize()
    auth_repository(app.state.database).create_user(
        "admin", hash_password("test-password-123"),
    )
    # Normal lifespan mirrors default production tenants to Engine. This
    # isolated smoke suppresses background workers, so mirror them explicitly.
    for tenant in app.state.tenants.repository.list_tenants():
        if tenant["status"] == "active":
            app.state.engine.ensure_tenant(
                tenant["id"], tenant["purpose"], tenant["status"],
            )
    app.router.lifespan_context = _isolated_http_only_lifespan
    return app


def login(client):
    return client.post("/api/auth/login", json={
        "username": "admin", "password": "test-password-123",
    })


def _tenant(http, purpose, name):
    if purpose == "production":
        rows = http.get("/api/tenants").json()
        return next(row for row in rows if row["purpose"] == "production")
    templates = {row["purpose"]: row for row in http.get("/api/tenants/templates").json()}
    response = http.post("/api/tenants", json={"template_id": templates[purpose]["id"], "name": name})
    assert response.status_code == 201, response.text
    return response.json()


def test_development_tenant_backtests_an_issued_package(tmp_path, archive):
    # The real synthetic Nautilus Catalog is seeded in disposable Engine Docker.
    app = _client(tmp_path)
    window = {"start": "2024-01-01", "end": "2024-01-02", "interval": "1m"}
    with TestClient(app) as http:
        login(http)
        created = http.post("/api/strategy-packages", json={
            "archive": base64.b64encode(archive).decode(),
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

    # Release the disposable business database engine explicitly.
    app.state.database.close()


def _wait_http_engine():
    from core.engine_contracts import ENGINE_CONTRACT_HEADER, ENGINE_CONTRACT_VERSION

    deadline = monotonic() + 60
    with httpx.Client(timeout=3, trust_env=False) as http:
        while monotonic() < deadline:
            try:
                response = http.get(
                    os.environ["ENGINE_URL"].rstrip("/") + "/health",
                    headers={
                        "Authorization": f'Bearer {os.environ["ENGINE_TOKEN"]}',
                        ENGINE_CONTRACT_HEADER: ENGINE_CONTRACT_VERSION,
                    },
                )
                if (response.status_code == 200
                        and response.headers.get(ENGINE_CONTRACT_HEADER) == ENGINE_CONTRACT_VERSION):
                    return
            except httpx.TransportError:
                pass
            sleep(0.5)
    raise RuntimeError("Disposable multi-tenant Engine HTTP did not become ready")


def main():
    root, state, package = _environment()
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
                 "http_proxy", "https_proxy", "all_proxy"):
        os.environ.pop(name, None)
    os.environ["NO_PROXY"] = "127.0.0.1,localhost"
    os.environ["no_proxy"] = "127.0.0.1,localhost"
    for name in ("DATABASE_URL", "DATABASE_URL_FILE", "SYSTEM_CREDENTIAL_KEY",
                 "ETL_CREDENTIAL_KEY", "BINANCE_API_KEY", "BINANCE_API_SECRET"):
        if name in os.environ:
            raise RuntimeError(f"Unexpected host secret/database setting: {name}")
    sys.path.insert(0, str(root))
    import api
    import core

    assert Path(api.__file__).resolve().is_relative_to(root)
    assert Path(core.__file__).resolve().is_relative_to(root)
    assert "engine" not in sys.modules and "nautilus_trader" not in sys.modules
    _wait_http_engine()
    test_development_tenant_backtests_an_issued_package(
        state, _zip_strategy_directory(package),
    )
    assert "engine" not in sys.modules and "nautilus_trader" not in sys.modules
    print("PASS: migrated legacy /api business backtest through independent Engine HTTP")


if __name__ == "__main__":
    main()
