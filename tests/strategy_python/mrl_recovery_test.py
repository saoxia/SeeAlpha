"""Recovery confirmation must stay causal, price-capped and conservatively evaluated."""
import json
from decimal import Decimal
from types import SimpleNamespace

import pytest
from nautilus_trader.backtest import BacktestEngine
from nautilus_trader.config import BacktestEngineConfig, LoggerConfig
from nautilus_trader.execution import ProbabilisticFillModel as FillModel
from nautilus_trader.model import AccountType, Bar, Money, OmsType

from nautilus_support import TestInstrumentProvider, instrument_fee_model
from strategy_python._research.mrl_recovery_20260922.run import decision, experiments
from strategy_python.mean_reversion_long_entry_5min.preset import create_config as previous_config
from strategy_python.mean_reversion_long_recovery_5min.admission import (
    FIVE_MINUTES,
    ReclaimAdmission,
    ReclaimQuote,
    recovery_price,
    reward_cost_allowed,
)
from strategy_python.mean_reversion_long_recovery_5min.preset import create_config
from strategy_python.mean_reversion_long_recovery_5min.strategy import (
    RecoveryMeanReversionLong5MinStrategy,
)


def bar(index, close, high):
    return SimpleNamespace(ts_end=index * FIVE_MINUTES, close=close, high=high)


def test_confirmation_freezes_prior_bands_and_only_uses_completed_recovery_high():
    model = ReclaimAdmission()
    model.update(bar(1, 101, 104), None)
    model.update(bar(2, 99, 102), (100, 110, 120))
    quote = model.update(bar(3, 103, 105), (95, 115, 135))
    assert quote.bands == (100, 110, 120) and quote.high == 105
    assert model.current(quote.created_ns - 1) is None
    assert model.current(quote.expires_ns) is None
    model.update(bar(5, 99, 105), (100, 110, 120))
    assert model.update(bar(6, 103, 105), (100, 110, 120)) is None
    model.reset()
    assert model.current(quote.created_ns) is None


@pytest.mark.parametrize("close", [105, 105.001, 105.009, 105.01])
def test_new_limit_uses_current_completed_close_and_tradable_precision(close):
    quote = ReclaimQuote((100, 110, 120), FIVE_MINUTES, 2 * FIVE_MINUTES, 104)
    limit = recovery_price(quote, close, FIVE_MINUTES + 1, Decimal("0.01"), 2)
    assert limit % Decimal("0.01") == 0
    assert Decimal(str(close)) * Decimal("1.0002") <= limit
    assert limit < Decimal(str(close)) * Decimal("1.0002") + Decimal("0.01")


def test_confirmation_cannot_use_own_close_expired_signal_or_touch_only():
    quote = ReclaimQuote((100, 110, 120), FIVE_MINUTES, 2 * FIVE_MINUTES, 104)
    for close, now in (
        (105, FIVE_MINUTES),
        (105, 2 * FIVE_MINUTES),
        (104, FIVE_MINUTES + 1),
        (None, FIVE_MINUTES + 1),
    ):
        assert recovery_price(quote, close, now, "0.01", 2) is None


def test_new_price_reduces_reward_and_can_fail_cost_gate_even_when_old_entry_passed():
    quote = ReclaimQuote((100, 101, 102), 0, FIVE_MINUTES, 100.8)
    limit = recovery_price(quote, 100.9, 1, Decimal("0.01"), 2)
    assert reward_cost_allowed(100, 101, 0.0005, 2, 2)
    assert not reward_cost_allowed(limit, 101, 0.0005, 2, 2)
    assert recovery_price(quote, 102, 1, "0.01", 2) is None


def test_control_reproduces_previous_candidate_and_risk_budget_does_not_increase():
    previous = json.loads(previous_config().json())
    control = json.loads(create_config(variant="control").json())
    for key, value in previous.items():
        assert control[key] == value, key
    assert not control["entry_recovery_breakout"]
    candidate = create_config()
    assert candidate.max_units == 1 and candidate.cycle_risk_fraction == 0.075
    assert candidate.hard_stop_distance == 0.25


@pytest.mark.parametrize("path", ["no_break", "break", "target_passed", "expired"])
@pytest.mark.parametrize("strict", [False, True])
def test_native_entry_waits_for_later_closed_minute_and_respects_cap(path, strict):
    instrument = TestInstrumentProvider.ethusdt_perp_binance()
    config = create_config(asset="ETH")
    minute = FIVE_MINUTES // 5
    start = 1785542400000000000
    created = start + minute - 1_000_000

    class Probe(RecoveryMeanReversionLong5MinStrategy):
        def __init__(self, config):
            super().__init__(config)
            self.features.aggregate = lambda b: dict.fromkeys((5, 15, 60, 360, 1440))
            self.features.bollinger = (1900, 2200, 2500)
            self.features.adverse_ma99_ratio = lambda s: 0
            self.risk_model.size_initial_entry = lambda *a: {
                "unit_quantity": Decimal("0.100"),
                "risk_per_quantity": Decimal(1),
                "planned_risk": Decimal("0.1"),
                "risk_context": {},
            }
            self.entry_admission.quote = ReclaimQuote(
                (1900, 2200, 2500), created, created + FIVE_MINUTES, 2050
            )
            self.submitted = []

        def apply_entry_target(self, state, target):
            self.submitted.append(self.clock.timestamp_ns())
            super().apply_entry_target(state, target)

    strategy = Probe(config)
    values = [(2000, 2050, 1999, 2000)]
    following = {
        "no_break": (2000, 2050, 1999, 2000),
        "break": (2000, 2050.2, 1999, 2050.1),
        "target_passed": (2250, 2251, 2249, 2250),
        "expired": (2000, 2050, 1999, 2000),
    }[path]
    values += [following] * 7
    if path == "expired":
        values[-1] = (2060, 2061, 2059, 2060)
    bars = []
    for index, prices in enumerate(values):
        ts = start + (index + 1) * minute - 1_000_000
        bars.append(
            Bar(
                config.bar_type,
                *(instrument.make_price(p) for p in prices),
                instrument.make_qty(100),
                ts,
                ts,
            )
        )
    engine = BacktestEngine(config=BacktestEngineConfig(logging=LoggerConfig(bypass_logging=True)))
    try:
        engine.add_venue(
            venue=instrument.id.venue,
            oms_type=OmsType.HEDGING,
            account_type=AccountType.MARGIN,
            base_currency=None,
            starting_balances=[Money(500, instrument.quote_currency)],
            default_leverage=Decimal(5),
            fill_model=FillModel(
                prob_fill_on_limit=0.0 if strict else 1.0,
                prob_slippage=1.0 if strict else 0.0,
                random_seed=20260922,
            ),
            fee_model=instrument_fee_model(instrument),
        )
        engine.add_instrument(instrument)
        engine.add_strategy(strategy)
        engine.add_data(bars)
        engine.run()
        orders = [o for o in engine.cache.orders() if any(":initial:" in t for t in o.tags or [])]
        if path != "break":
            assert not orders and not strategy.submitted
            return
        assert len(orders) == len(strategy.submitted) == 1
        order = orders[0]
        assert order.status.name == "FILLED" and order.time_in_force.name == "GTD"
        assert strategy.submitted[0] > created
        assert order.expire_time_ns == created + FIVE_MINUTES
        for event in order.events():
            if type(event).__name__ == "OrderFilled":
                assert event.ts_event >= strategy.submitted[0]
                assert event.last_px.as_decimal() <= order.price.as_decimal()
                assert event.ts_event < order.expire_time_ns
    finally:
        engine.dispose()


def test_decision_requires_both_references_and_strict_and_nontrivial_samples():
    good = {
        "net_pnl": 10,
        "review_marked_pnl": 4,
        "max_drawdown_pct": 5,
        "trades": 200,
        "review_closed_trades": 40,
        "pnl_without_best5": 2,
        "invalid_entry_orders": 0,
        "off_tick_fields": 0,
        "cashflow_error": 0,
    }
    rows = {r["id"]: dict(good) for r in experiments()}
    refs = {
        f"{a}_{v}_fee{f}": {"net_pnl": -10, "review_marked_pnl": -3, "max_drawdown_pct": 10}
        for a in ("BTC", "ETH")
        for v in ("baseline", "reclaim")
        for f in (0, 5)
    }
    assert decision(rows, refs)["passed"]
    refs["ETH_reclaim_fee0"]["net_pnl"] = 11
    assert not decision(rows, refs)["consistent_improvement"]
    rows["ETH_recovery_fee5_strict"]["net_pnl"] = -1
    assert not decision(rows, refs)["passed"]
    rows["ETH_recovery_fee5_strict"]["net_pnl"] = 10
    rows["BTC_recovery_fee0"]["trades"] = 0
    assert not decision(rows, refs)["passed"]
