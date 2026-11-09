"""Actual cross-process strategy load and Nautilus historical-backtest smoke.

This runs on an isolated Platform Python environment. The Engine is a disposable
Docker container with a pre-seeded synthetic Catalog, not the legacy engine/
source tree. Only the EngineClient HTTP v2 boundary is used from Platform.
"""

import base64
import hashlib
import io
import ipaddress
import os
import sys
import zipfile
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlsplit


def _inputs():
    if os.environ.get("SEEALPHA_SMOKE_DISPOSABLE") != "1":
        raise RuntimeError("Refusing to run backtest against an unmarked Engine")
    root = Path(os.environ["SEEALPHA_ISOLATED_PLATFORM_ROOT"]).resolve()
    url = os.environ["ENGINE_URL"].rstrip("/")
    parsed = urlsplit(url)
    if (
        parsed.scheme != "http" or not parsed.hostname
        or not ipaddress.ip_address(parsed.hostname).is_loopback
        or parsed.username or parsed.password or parsed.query or parsed.fragment
        or parsed.path not in ("", "/")
    ):
        raise RuntimeError("Cross-process backtest requires loopback HTTP only")
    token = os.environ["ENGINE_TOKEN"]
    if not token or token == "seealpha-local-engine":
        raise RuntimeError("A disposable private Engine token is required")
    tenant = os.environ["ENGINE_SMOKE_TENANT_ID"]
    if not tenant.isdecimal() or int(tenant) < 100000000:
        raise RuntimeError("Invalid disposable tenant identity")
    if (root / "engine").exists() or (root / "nautilus_support.py").exists():
        raise RuntimeError("Platform test source must not include Engine internals")
    package = root / "packages" / "ema_crossover"
    if not (package / "seealpha-package.json").is_file():
        raise RuntimeError("Portable EMA fixture was not copied into isolated Platform")
    return root, url, token, tenant, package


def _archive(root):
    """Stable fixture ZIP: exact member ordering/flags used by Engine serializer."""
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


def _must_conflict(action, expected):
    from core.engine_contracts import EngineConflict
    try:
        action()
    except EngineConflict as error:
        assert expected in str(error), str(error)
    else:
        raise AssertionError(f"Expected Engine conflict mentioning {expected!r}")


def _wait_success(client, tenant, identity):
    """Poll an already-running Engine worker; never create a process/container."""
    from time import monotonic, sleep

    deadline = monotonic() + 180
    while monotonic() < deadline:
        job = client.task(tenant, identity)
        if job["status"] == "SUCCEEDED":
            return job
        if job["status"] in {"FAILED", "CANCELLED", "INTERRUPTED"}:
            raise AssertionError(f"Actual background Engine backtest failed: {job}")
        assert job["status"] in {"PENDING", "RUNNING"}, job
        sleep(0.25)
    raise TimeoutError(f"Timed out waiting for backtest job {identity}")


def main():
    root, url, token, tenant, package = _inputs()
    # Never forward a test bearer token to a host-configured HTTP proxy.
    for proxy in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
                  "http_proxy", "https_proxy", "all_proxy"):
        os.environ.pop(proxy, None)
    os.environ["NO_PROXY"] = "127.0.0.1,localhost"
    os.environ["no_proxy"] = "127.0.0.1,localhost"

    sys.path.insert(0, str(root))
    from api.engine_client import EngineClient
    from core.engine_contracts import QueryError

    assert Path(sys.modules["api"].__file__).resolve().is_relative_to(root)
    assert Path(sys.modules["core"].__file__).resolve().is_relative_to(root)
    assert "engine" not in sys.modules and "nautilus_trader" not in sys.modules

    client = EngineClient(root / "temporary-state", url=url, token=token)
    account = "smoke-paper"
    start, end = "2024-01-01", "2024-01-02"
    try:
        assert client.ensure_tenant(tenant, "development")["purpose"] == "development"
        assert client.latest_backtest(tenant, account) is None

        _must_conflict(
            lambda: client.run_backtest(tenant, account, start, end, "1m", "1000.50", "USDT"),
            "没有已下发",
        )
        _must_conflict(lambda: client.runnable(tenant, "live"), "开发租户")

        raw_archive = _archive(package)
        encoded = base64.b64encode(raw_archive).decode("ascii")
        parameters = {
            "symbol": "ETHUSDT", "fast_period": 3, "slow_period": 5,
            "allocation": "1", "risk_limit": "0.5",
        }
        inspection = client.inspect_strategy(encoded, parameters, tenant_id=tenant)
        metadata = inspection["metadata"]
        assert metadata["digest"] == hashlib.sha256(raw_archive).hexdigest()
        assert metadata["name"] == "ema_crossover"
        assert metadata["entry_point"] == "strategy:EmaCrossover"
        prepared = inspection["parameters"]

        for number in (1, 2):
            loaded = client.receive_strategy(tenant, {
                "account_id": account, "issuance_id": f"smoke-{number}",
                "code_key": metadata["name"], "version": metadata["version"],
                "grant_purpose": "development", "parameters": prepared,
                "entry_point": metadata["entry_point"],
                "package_hash": metadata["digest"], "archive": encoded,
            })
            assert str(loaded["tenant_id"]) == tenant
            assert loaded["grant_purpose"] == "development"

        _must_conflict(
            lambda: client.run_backtest(tenant, account, start, end, "1m", "1000.50", "USDT"),
            "订阅",
        )
        client.note_subscription(
            tenant, "futures_um", "ETHUSDT", "intent", "configured",
            {"history_enabled": True},
        )

        result = client.run_backtest(
            tenant, account, start, end, "1m", "1000.50", "USDT",
        )
        assert str(result["tenant_id"]) == tenant
        assert result["account_id"] == account
        assert result["symbol"] == "ETHUSDT"
        assert result["interval"] == "1m"
        assert result["bar_count"] == 19
        assert Decimal(result["starting_balance"]) == Decimal("1000.50")
        assert result["quote_currency"] == "USDT"
        assert Decimal(result["ending_balance"]) < Decimal("1000.50")

        assert [Decimal(item["quantity"]) for item in result["holdings"]] == [
            Decimal(1), Decimal(0), Decimal(1), Decimal(0),
        ]
        assert [(item["side"], Decimal(item["quantity"])) for item in result["orders"]] == [
            ("BUY", Decimal("0.5")), ("SELL", Decimal("0.5")),
        ]
        assert [(item["side"], Decimal(item["quantity"])) for item in result["fills"]] == [
            ("BUY", Decimal("0.5")), ("SELL", Decimal("0.5")),
        ]
        assert all(row["tags"] == ["portfolio-net-delta"] for row in result["orders"])
        assert all(row["tags"] == ["portfolio-net-delta"] for row in result["fills"])
        assert set(result["coordinator_ids"]) == {
            row["strategy_id"] for row in result["orders"]
        }
        assert set(result["coordinator_ids"]).isdisjoint(result["member_ids"])
        assert Decimal(result["positions"][0]["quantity"]) == Decimal(0)
        assert client.latest_backtest(tenant, account) == result
        second_account = "other-paper"
        assert client.latest_backtest(tenant, second_account) is None

        # Two distinct backtests must share the *same resident* Engine worker
        # without sharing virtual capital, task results or account journals.
        # No Docker command is ever invoked by Platform EngineClient.
        second_load = client.receive_strategy(tenant, {
            "account_id": second_account, "issuance_id": "smoke-other",
            "code_key": metadata["name"], "version": metadata["version"],
            "grant_purpose": "development", "parameters": prepared,
            "entry_point": metadata["entry_point"],
            "package_hash": metadata["digest"], "archive": encoded,
        })
        assert second_load["account_id"] == second_account

        first_parameters = {
            "account_id": account, "start": start, "end": end,
            "interval": "1m", "starting_balance": "1000.50",
            "quote_currency": "USDT",
        }
        second_parameters = {
            **first_parameters,
            "account_id": second_account, "starting_balance": "2000.50",
        }
        first_job = client.submit_task(
            tenant, "backtest", first_parameters,
            idempotency_key="disposable-reuse-1",
        )
        replay = client.submit_task(
            tenant, "backtest", first_parameters,
            idempotency_key="disposable-reuse-1",
        )
        assert replay["id"] == first_job["id"]
        second_job = client.submit_task(
            tenant, "backtest", second_parameters,
            idempotency_key="disposable-reuse-2",
        )
        assert first_job["id"] != second_job["id"]
        assert first_job["status"] in {"PENDING", "RUNNING", "SUCCEEDED"}
        assert second_job["status"] in {"PENDING", "RUNNING", "SUCCEEDED"}

        _wait_success(client, tenant, first_job["id"])
        _wait_success(client, tenant, second_job["id"])
        first_result = client.task_result(tenant, first_job["id"])
        second_result = client.task_result(tenant, second_job["id"])
        assert first_result["account_id"] == account
        assert second_result["account_id"] == second_account
        assert str(first_result["tenant_id"]) == tenant
        assert str(second_result["tenant_id"]) == tenant
        assert Decimal(first_result["starting_balance"]) == Decimal("1000.50")
        assert Decimal(second_result["starting_balance"]) == Decimal("2000.50")
        assert first_result["bar_count"] == second_result["bar_count"] == 19
        assert Decimal(first_result["ending_balance"]) < Decimal("1000.50")
        assert Decimal(second_result["ending_balance"]) < Decimal("2000.50")
        assert [row["side"] for row in first_result["orders"]] == ["BUY", "SELL"]
        assert [row["side"] for row in second_result["orders"]] == ["BUY", "SELL"]
        assert first_result["fills"] and second_result["fills"]
        assert client.task_result(tenant, first_job["id"]) == first_result
        assert client.latest_backtest(tenant, account) == first_result
        assert client.latest_backtest(tenant, second_account) == second_result
        records = client.list_tasks(tenant)
        assert {first_job["id"], second_job["id"]}.issubset(
            {row["id"] for row in records if row["status"] == "SUCCEEDED"}
        )

        # A historical simulation must not create live exchange ledger facts.
        for account_id in (account, second_account):
            for resource in ("orders", "fills", "positions"):
                live = client._sync(
                    "GET", f"/tenants/{tenant}/accounts/{account_id}/{resource}",
                    timeout=5, params={"environment": "sandbox"},
                )
                assert live[resource] == [], (account_id, resource, live)
        try:
            client._sync(
                "GET", f"/tenants/{int(tenant) + 1}/accounts/{account}/backtests/latest",
                timeout=5,
            )
        except QueryError:
            pass
        else:
            raise AssertionError("A fixed-tenant Engine exposed another tenant's results")

        assert "engine" not in sys.modules and "nautilus_trader" not in sys.modules
        assert "api_key" not in str(result) and "api_secret" not in str(result)
        print(
            "PASS: real Nautilus historical backtest across isolated Platform HTTP; "
            "strategy ZIP, tenant safety, account balance, orders, fills and position; "
            "two queued backtests reused one Engine with isolated job results"
        )
    finally:
        client.close()


if __name__ == "__main__":
    main()
