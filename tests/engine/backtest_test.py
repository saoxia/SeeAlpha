"""Development tenants backtest issued packages against catalog history."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from engine.app import create_app
from engine.config import EngineConfig
from engine.runtime import EngineConflict, EngineRuntime
from tests.engine.strategy_load_test import ema_parameters, issued_payload
from tests.fixtures.engine_catalog import write_crossover_catalog
from tests.fixtures.strategy_packages import CLOSES, crossover_bars, ema_archive


def _window(start):
    return {
        "start": start.isoformat(),
        "end": (start + timedelta(minutes=len(CLOSES) + 2)).isoformat(),
        "interval": "1m",
        "starting_balance": "1000.50",
        "quote_currency": "USDT",
    }


def _intent(runtime, tenant_id, symbol="ETHUSDT"):
    runtime.note_subscription(
        tenant_id, "market-data", "futures_um", symbol, "intent", "configured",
        {"history_enabled": True},
    )


def _issue(runtime, tenant_id, purpose, issuance_id, allocation="1", risk_limit="1"):
    return runtime.receive_strategy(tenant_id, issued_payload(
        ema_archive(), purpose, issuance_id, ema_parameters(allocation, risk_limit),
    ))


def test_development_backtest_clips_targets_and_persists_a_result(tmp_path):
    start = datetime(2024, 1, 1, tzinfo=UTC)
    write_crossover_catalog(tmp_path / "data" / "catalog", start)
    runtime = EngineRuntime(tmp_path / "state", tmp_path)
    try:
        runtime.ensure_tenant("dev", "development")
        runtime.ensure_tenant("other", "development")
        _intent(runtime, "dev")
        _issue(runtime, "dev", "development", "1", risk_limit="0.5")
        _issue(runtime, "dev", "development", "2", risk_limit="0.5")
        _issue(runtime, "other", "development", "9", allocation="1", risk_limit="1")
        result = runtime.run_backtest("dev", "desk", **_window(start))
        again = runtime.latest_backtest("dev", "desk")
    finally:
        runtime.close()
    assert again == result
    assert result["tenant_id"] == "dev"
    assert result["bar_count"] == len(CLOSES)
    assert result["starting_balance"] == "1000.50"
    assert result["quote_currency"] == "USDT"
    assert Decimal(result["ending_balance"]) < Decimal(result["starting_balance"])
    assert [row["quantity"] for row in result["holdings"]] == ["1", "0", "1", "0"]
    assert {row["code_key"] for row in result["holdings"]} == {"ema_crossover"}
    assert [(row["side"], Decimal(row["quantity"])) for row in result["orders"]] == [
        ("BUY", Decimal("0.5")),
        ("SELL", Decimal("0.5")),
    ]
    assert [(row["side"], Decimal(row["quantity"])) for row in result["fills"]] == [
        ("BUY", Decimal("0.5")),
        ("SELL", Decimal("0.5")),
    ]
    assert all(row["tags"] == ["portfolio-net-delta"] for row in result["orders"])
    assert all(row["tags"] == ["portfolio-net-delta"] for row in result["fills"])
    assert {row["strategy_id"] for row in result["orders"]} == set(result["coordinator_ids"])
    assert set(result["coordinator_ids"]).isdisjoint(result["member_ids"])
    assert Decimal(result["positions"][0]["quantity"]) == Decimal(0)
    assert "api_key" not in str(result)


def test_backtest_gates_reject_archived_production_and_unissued_packages(tmp_path):
    start = datetime(2024, 1, 1, tzinfo=UTC)
    write_crossover_catalog(tmp_path / "data" / "catalog", start)
    runtime = EngineRuntime(tmp_path / "state", tmp_path)
    window = _window(start)
    try:
        runtime.ensure_tenant("dev", "development")
        runtime.ensure_tenant("prod", "production")
        runtime.ensure_tenant("old", "development", "archived")
        _intent(runtime, "prod")
        _intent(runtime, "dev", "BTCUSDT")
        _issue(runtime, "prod", "production", "2")
        with pytest.raises(EngineConflict, match="不能交易或回测"):
            runtime.run_backtest("old", "desk", **window)
        with pytest.raises(EngineConflict, match="正式授权"):
            _issue(runtime, "prod", "development", "3")
        with pytest.raises(EngineConflict, match="只在开发租户"):
            runtime.run_backtest("prod", "desk", **window)
        with pytest.raises(EngineConflict, match="没有已下发"):
            runtime.run_backtest("dev", "desk", **window)
        _issue(runtime, "dev", "development", "1")
        with pytest.raises(EngineConflict, match="行情订阅"):
            runtime.run_backtest("dev", "desk", **window)
        _intent(runtime, "dev")
        with pytest.raises(EngineConflict, match="没有可用的本地 K 线"):
            runtime.run_backtest(
                "dev", "desk", start="2024-02-01T00:00:00+00:00",
                end="2024-02-02T00:00:00+00:00", interval="1m",
                starting_balance="1000", quote_currency="USDT",
            )
        with pytest.raises(EngineConflict, match="366"):
            runtime.run_backtest(
                "dev", "desk", start="2020-01-01", end="2024-01-01", interval="1m",
                starting_balance="1000", quote_currency="USDT",
            )
        with pytest.raises(EngineConflict, match="报价币种"):
            runtime.run_backtest("dev", "desk", **{**window, "quote_currency": "USDC"})
        runtime.ensure_tenant("dev", "development", "archived")
        with pytest.raises(EngineConflict, match="不能交易或回测"):
            runtime.run_backtest("dev", "desk", **window)
        assert runtime.store.strategy_loads("prod")[0]["grant_purpose"] == "production"
        assert runtime.latest_backtest("prod", "desk") is None
    finally:
        runtime.close()


def test_preview_still_uses_supplied_bars_without_a_subscription(tmp_path):
    runtime = EngineRuntime(tmp_path / "state", tmp_path)
    try:
        runtime.ensure_tenant("dev", "development")
        _issue(runtime, "dev", "development", "1")
        preview = runtime.preview_strategy("dev", "desk", crossover_bars())
    finally:
        runtime.close()
    assert preview["orders"][0]["tags"] == ["portfolio-net-delta"]
    assert "fills" not in preview
    assert "ending_balance" not in preview


def test_engine_http_backtest_is_tenant_scoped(tmp_path):
    start = datetime(2024, 1, 1, tzinfo=UTC)
    write_crossover_catalog(tmp_path / "data" / "catalog", start)
    app = create_app(EngineConfig(tmp_path / "state", token="engine-test", project=tmp_path))
    headers = {"Authorization": "Bearer engine-test"}
    body = issued_payload(ema_archive(), "development", "4", ema_parameters())
    with TestClient(app) as client:
        assert client.put(
            "/tenants/dev", headers=headers, json={"purpose": "development"},
        ).status_code == 200
        assert client.put(
            "/tenants/prod", headers=headers, json={"purpose": "production"},
        ).status_code == 200
        created = client.post("/tenants/dev/strategy-loads", headers=headers, json=body)
        assert created.status_code == 200, created.text
        client.post("/tenants/dev/accounts/market-data/market/subscriptions", headers=headers, json={
            "product": "futures_um", "symbol": "ETHUSDT", "role": "intent",
            "state": "configured", "progress": {"history_enabled": True},
        })
        missing = client.get("/tenants/dev/accounts/desk/backtests/latest", headers=headers)
        assert missing.status_code == 404
        blocked = client.post("/tenants/prod/accounts/desk/backtests", headers=headers, json=_window(start))
        assert blocked.status_code == 409
        assert "开发租户" in blocked.json()["detail"]
        started = client.post("/tenants/dev/accounts/desk/backtests", headers=headers, json=_window(start))
        assert started.status_code == 200, started.text
        assert started.json()["fills"][0]["tags"] == ["portfolio-net-delta"]
        assert Decimal(started.json()["starting_balance"]) == Decimal("1000.50")
        latest = client.get("/tenants/dev/accounts/desk/backtests/latest", headers=headers)
        assert latest.json() == started.json()
        other = client.get("/tenants/prod/accounts/desk/backtests/latest", headers=headers)
        assert other.status_code == 404
