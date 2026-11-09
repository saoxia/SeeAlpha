"""Engine-free business policy boundaries for strategy issuance and backtests."""

import pytest
from fastapi import HTTPException

from api.strategies.service import StrategyStore


class FakeTenants:
    def __init__(self, purpose, accounts=()):
        self.purpose = purpose
        self.repository = self
        self.accounts = list(accounts)

    def require_active(self, tenant_id):
        assert tenant_id == 7
        return {"id": tenant_id, "purpose": self.purpose, "status": "active"}

    def list_virtual_accounts(self, tenant_id):
        assert tenant_id == 7
        return self.accounts


class FakeEngine:
    def __init__(self):
        self.calls = []

    def runnable(self, tenant_id, action):
        self.calls.append(("runnable", tenant_id, action))

    def run_backtest(self, *args):
        self.calls.append(("run_backtest", *args))
        return {"kind": "backtest", "status": "completed"}

    def submit_task(self, tenant_id, action, payload, *, idempotency_key=None):
        self.calls.append(("submit_task", tenant_id, action, payload, idempotency_key))
        return {"id": "a" * 32, "kind": action, "status": "PENDING"}


def _service(purpose, accounts=()):
    service = object.__new__(StrategyStore)
    service.tenants = FakeTenants(purpose, accounts)
    service.engine = FakeEngine()
    return service


def test_backtests_reject_production_and_unowned_account_before_engine():
    for method in ("backtest", "submit_backtest"):
        production = _service("production")
        with pytest.raises(HTTPException, match="开发租户") as rejected:
            getattr(production, method)(
                7, 1, "2024-01-01", "2024-01-02", "1m"
            )
        assert rejected.value.status_code == 409
        assert production.engine.calls == []

        development = _service("development")
        with pytest.raises(HTTPException, match="账户不属于") as missing:
            getattr(development, method)(
                7, 99, "2024-01-01", "2024-01-02", "1m"
            )
        assert missing.value.status_code == 404
        assert development.engine.calls == []


def test_backtest_requests_forward_account_budget_and_idempotency():
    service = _service("development", [{
        "id": 3, "initial_capital": "1000.50", "quote_currency": "USDT",
    }])
    result = service.backtest(7, 3, "2024-01-01", "2024-01-02", "5m")
    assert result["status"] == "completed"
    assert service.engine.calls == [
        ("runnable", 7, "backtest"),
        ("run_backtest", 7, 3, "2024-01-01", "2024-01-02", "5m", "1000.50", "USDT"),
    ]
    service.engine.calls.clear()

    job = service.submit_backtest(
        7, 3, "2024-01-01", "2024-01-02", "1m",
        idempotency_key="study-1",
    )
    assert job["status"] == "PENDING"
    assert service.engine.calls == [
        ("runnable", 7, "backtest"),
        ("submit_task", 7, "backtest", {
            "account_id": "3",
            "start": "2024-01-01",
            "end": "2024-01-02",
            "interval": "1m",
            "starting_balance": "1000.50",
            "quote_currency": "USDT",
        }, "study-1"),
    ]


def test_ungranted_production_strategy_cannot_reach_engine():
    service = _service("production")

    class Repository:
        def get_version(self, version_id):
            assert version_id == 17
            return {"id": 17, "package_id": 5}

        def get_package(self, package_id):
            assert package_id == 5
            return {"code_key": "ema_crossover"}

        def find_grant(self, version_id, purpose):
            assert (version_id, purpose) == (17, "production")
            return None

    service.repository = Repository()
    with pytest.raises(HTTPException, match="正式授权") as rejected:
        service.issue(7, "1", 17, None)
    assert rejected.value.status_code == 409
    assert service.engine.calls == []


def test_strategy_business_module_only_calls_engine_protocol_and_never_imports_packages():
    from pathlib import Path

    path = Path(__file__).resolve().parents[2] / "api/strategies/service.py"
    source = path.read_text(encoding="utf-8")
    assert "from engine." not in source
    assert "from nautilus_trader" not in source
    assert "open_package(" not in source
    assert "inspect_strategy(" in source
