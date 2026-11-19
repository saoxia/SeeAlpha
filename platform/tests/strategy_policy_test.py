"""Engine-free business policy boundaries for strategy issuance and backtests."""

import pytest
from api.strategies.service import StrategyStore
from fastapi import HTTPException


class FakeTenants:
    def __init__(self, purpose, accounts=()):
        self.purpose = purpose
        self.repository = self
        self.accounts = list(accounts)

    def require_active(self, tenant_id):
        assert tenant_id == 7
        return {"id": tenant_id, "purpose": self.purpose, "status": "active"}

    def require_tenant(self, tenant_id):
        return self.require_active(tenant_id)

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

    def run_funding_carry_backtest(self, *args):
        self.calls.append(("run_funding_carry_backtest", *args))
        return {"mode": "native_dual_venue_development_only", "bar_count": 4}

    def run_issued_spot_perp_backtest(self, *args):
        self.calls.append(("run_issued_spot_perp_backtest", *args))
        return {"mode": "issued_spot_perp_development_only", "fills": []}

    def latest_issued_spot_perp_backtest(self, *args):
        self.calls.append(("latest_issued_spot_perp_backtest", *args))
        return {"mode": "issued_spot_perp_development_only"}

    def run_protected_option_backtest(self, *args):
        self.calls.append(("run_protected_option_backtest", *args))
        return {"mode": "native_protected_options_development_only", "bar_count": 4}

    def latest_funding_carry_backtest(self, tenant_id, account_id):
        self.calls.append(("latest_funding_carry_backtest", tenant_id, account_id))
        return {"mode": "native_dual_venue_development_only"}

    def latest_protected_option_backtest(self, tenant_id, account_id):
        self.calls.append(("latest_protected_option_backtest", tenant_id, account_id))
        return {"mode": "native_protected_options_development_only"}


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


def test_basket_backtests_allow_only_development_virtual_usdt_budget():
    for purpose in ("development", "production"):
        service = _service(purpose, [{
            "id": 3, "initial_capital": "2000", "quote_currency": "USDT",
        }])
        args = (7, 3, "funding_carry", "2024-01-01", "2024-01-02", "1m",
                "800", "900", "1900")
        if purpose == "production":
            with pytest.raises(HTTPException, match="开发"):
                service.basket_backtest(*args)
            assert not service.engine.calls
            continue
        result = service.basket_backtest(*args)
        assert result["bar_count"] == 4
        assert service.engine.calls == [
            ("runnable", 7, "backtest"),
            ("run_funding_carry_backtest", 7, 3,
             "2024-01-01", "2024-01-02", "1m", "800", "900", "1900"),
        ]
        service.engine.calls.clear()
        options = service.basket_backtest(
            7, 3, "protected_options", "2024-01-01", "2024-01-02",
            "5m", "400", "500", "1200",
        )
        assert options["mode"] == "native_protected_options_development_only"
        assert service.engine.calls[-1] == (
            "run_protected_option_backtest", 7, 3, "2024-01-01",
            "2024-01-02", "5m", "400", "500", "1200",
        )
        service.engine.calls.clear()
        for invalid in ("0", "-1", "NaN", "Infinity", "1e100"):
            with pytest.raises(HTTPException) as rejected:
                service.basket_backtest(
                    7, 3, "funding_carry", "2024-01-01", "2024-01-02",
                    "1m", invalid, "900", "1900",
                )
            assert rejected.value.status_code == 422
        with pytest.raises(HTTPException, match="超出"):
            service.basket_backtest(
                7, 3, "funding_carry", "2024-01-01", "2024-01-02",
                "1m", "1200", "900", "1900",
            )
        assert service.engine.calls == []


def test_basket_backtest_rejects_unowned_account_and_bad_currency_before_engine():
    missing = _service("development")
    with pytest.raises(HTTPException) as exc:
        missing.basket_backtest(
            7, 999, "funding_carry", "2024-01-01", "2024-01-02",
            "1m", "100", "100", "200",
        )
    assert exc.value.status_code == 404
    assert missing.engine.calls == []

    wrong_currency = _service("development", [{
        "id": 3, "initial_capital": "2000", "quote_currency": "USDC",
    }])
    with pytest.raises(HTTPException) as exc:
        wrong_currency.basket_backtest(
            7, 3, "protected_options", "2024-01-01", "2024-01-02",
            "1m", "100", "100", "200",
        )
    assert exc.value.status_code == 422
    assert wrong_currency.engine.calls == []


def test_basket_result_queries_keep_account_scope():
    service = _service("development", [{
        "id": 3, "initial_capital": "1000", "quote_currency": "USDT",
    }])
    assert service.latest_basket_backtest(7, 3, "funding_carry")["mode"].startswith("native_")
    assert service.engine.calls[-1] == ("latest_funding_carry_backtest", 7, "3")
    assert service.latest_basket_backtest(7, 3, "protected_options")["mode"].startswith("native_")
    assert service.engine.calls[-1] == ("latest_protected_option_backtest", 7, "3")
    with pytest.raises(HTTPException):
        service.latest_basket_backtest(7, 999, "funding_carry")


@pytest.mark.parametrize("code_key", [
    "demo_funding_carry", "demo_option_hedged_bollinger",
])
def test_research_only_system_basket_cannot_receive_or_use_production_grant(code_key):
    service = _service("production")

    class Repository:
        def get_version(self, version_id):
            assert version_id == 17
            return {"id": 17, "package_id": 5}

        def get_package(self, package_id):
            assert package_id == 5
            return {"id": 5, "owner_id": None, "code_key": code_key}

        def find_grant(self, version_id, purpose):
            # A legacy or mistakenly created grant must not bypass the gate.
            assert (version_id, purpose) == (17, "production")
            return {"id": 10, "purpose": purpose}

        def create_grant(self, *_args):
            pytest.fail("Production basket Demo must never receive a grant")

    service.repository = Repository()
    with pytest.raises(HTTPException, match="禁止正式授权") as denied:
        service.grant(17, "production")
    assert denied.value.status_code == 409
    with pytest.raises(HTTPException, match="禁止加载生产账户") as denied:
        service.issue(7, "1", 17, None)
    assert denied.value.status_code == 409
    assert service.engine.calls == []


def test_single_issued_strategy_basket_scopes_account_and_virtual_budget():
    service = _service("development", [{
        "id": 3, "initial_capital": "1000", "quote_currency": "USDT",
    }])

    class Repository:
        def list_issuances(self, tenant_id):
            assert tenant_id == 7
            return [
                {"id": 24, "account_id": 3, "purpose": "development"},
                {"id": 25, "account_id": 4, "purpose": "development"},
                {"id": 26, "account_id": 3, "purpose": "production"},
            ]

    service.repository = Repository()
    args = (7, 3, "24", "2024-01-01", "2024-01-02", "1m", "400", "500", "1200")
    result = service.issued_spot_perp_backtest(*args)
    assert result["mode"] == "issued_spot_perp_development_only"
    assert service.engine.calls == [
        ("runnable", 7, "backtest"),
        ("run_issued_spot_perp_backtest", *args),
    ]
    service.engine.calls.clear()
    for unknown in ("25", "26", "999"):
        with pytest.raises(HTTPException) as rejected:
            service.issued_spot_perp_backtest(*(
                7, 3, unknown, *args[3:],
            ))
        assert rejected.value.status_code == 404
        assert service.engine.calls == []
    with pytest.raises(HTTPException, match="账户初始资金"):
        service.issued_spot_perp_backtest(*(args[:6] + ("700", "500", "1200")))
    assert service.engine.calls == []
    latest = service.latest_issued_spot_perp_backtest(7, 3)
    assert latest["mode"] == "issued_spot_perp_development_only"
    assert service.engine.calls == [("latest_issued_spot_perp_backtest", 7, "3")]


def test_single_issued_strategy_basket_denies_production_without_engine_call():
    service = _service("production", [{
        "id": 3, "initial_capital": "1000", "quote_currency": "USDT",
    }])
    with pytest.raises(HTTPException, match="开发租户"):
        service.issued_spot_perp_backtest(
            7, 3, "24", "2024-01-01", "2024-01-02", "1m",
            "400", "500", "1200",
        )
    assert service.engine.calls == []
