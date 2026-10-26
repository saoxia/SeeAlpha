from dataclasses import replace
from decimal import Decimal

import pytest
from nautilus_trader.model import BarType, InstrumentId

from strategy_python._indicator.bars import CompletedBar
from strategy_python.pullback_short_1h import PullbackShort1hConfig, PullbackShort1hStrategy
from strategy_python.pullback_short_1h.indicators import Features, PullbackIndicators
from strategy_python.pullback_short_1h.risk import quantity_for_risk
from strategy_python.pullback_short_1h.signal import short_setup


def test_rebound_rejection_requires_trend_and_completed_reversal():
    f = Features(99, 102, 100, 100, 1, 104, True)
    assert short_setup(f)
    for g in (replace(f, down_daily=False), replace(f, previous_close=99), replace(f, close=101)):
        assert not short_setup(g)


def test_sizing_caps_loss_including_cost_and_exposure():
    qty = quantity_for_risk(
        Decimal(500),
        Decimal(2000),
        Decimal(2020),
        risk_fraction=0.005,
        max_notional_fraction=1,
        fee_buffer=0.0007,
        increment=Decimal(".001"),
    )
    assert qty * Decimal("22.814") <= Decimal("2.5")
    assert qty * 2000 <= 500
    assert qty == Decimal(".109")


def test_gap_resets_features_and_stale_trend_is_disabled():
    model = PullbackIndicators()
    step = 3600_000_000_000

    def bar(n):
        return CompletedBar((n - 1) * step, n * step, 100, 102, 99, 101)

    for n in range(1, 22):
        f = model.update_hour(bar(n))
    assert f is not None
    assert not f.down_daily
    assert model.update_hour(bar(23)) is None
    for n in range(24, 43):
        assert model.update_hour(bar(n)) is None
    assert model.update_hour(bar(43)) is not None


def test_daily_regime_uses_only_completed_day_and_expires_at_missing_midnight():
    model = PullbackIndicators()
    day = 86400_000_000_000
    hour = 3600_000_000_000
    for n in range(1, 21):
        price = 100 if n < 20 else 80
        model.update_daily(CompletedBar((n - 1) * day, n * day, price, price + 1, price - 5, price))
    assert model.down_daily
    for n in range(1, 24):
        end = 20 * day + n * hour
        f = model.update_hour(CompletedBar(end - hour, end, 90, 95, 85, 90))
    assert f.down_daily
    end = 21 * day
    f = model.update_hour(CompletedBar(end - hour, end, 90, 95, 85, 90))
    assert not f.down_daily
    model.update_daily(CompletedBar(21 * day, 22 * day, 80, 85, 75, 80))
    assert not model.down_daily  # Missing day resets the 20-day window.


@pytest.mark.parametrize(
    "parameters",
    [
        {"reward_risk": float("nan")},
        {"risk_fraction": 0.5},
        {"min_stop_fraction": 0.1, "max_stop_fraction": 0.03},
        {"max_holding_hours": 0},
        {"ma_period": 0},
    ],
)
def test_invalid_config_rejected(parameters):
    with pytest.raises((ValueError, TypeError)):
        PullbackShort1hStrategy(
            PullbackShort1hConfig(
                instrument_id=InstrumentId.from_str("ETHUSDT-PERP.BINANCE"),
                bar_type=BarType.from_str("ETHUSDT-PERP.BINANCE-1-MINUTE-LAST-EXTERNAL"),
                **parameters,
            )
        )


def test_portfolio_registry_creates_independent_member_and_rejects_fake_shared_budget():
    import json
    from datetime import UTC, datetime
    from pathlib import Path

    from strategy_python.portfolio import create_strategies, parse_portfolio

    data = json.loads(
        Path("strategy_python/_portfolio/mean_reversion_long_trend_both.json").read_text()
    )
    data["strategies"] = [{"id": "PBS", "strategy": "pullback_short_1h", "parameters": {}}]
    members, budget = create_strategies(datetime(2025, 1, 1, tzinfo=UTC), parse_portfolio(data))
    assert len(members) == 1 and isinstance(members[0], PullbackShort1hStrategy)
    assert budget is None and members[0].config.order_id_tag == "PBS"
    data["risk"] = {"mode": "shared", "total_fraction": 0.1}
    data["strategies"][0]["risk_weight"] = 1
    with pytest.raises(ValueError, match="尚未接入共享风险预算"):
        create_strategies(datetime(2025, 1, 1, tzinfo=UTC), parse_portfolio(data))
