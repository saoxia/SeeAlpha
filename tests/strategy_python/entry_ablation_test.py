"""Single-condition removal must preserve the other entry gates."""

import random
from decimal import Decimal
from types import SimpleNamespace

import pytest
from nautilus_trader.backtest import BacktestEngine
from nautilus_trader.config import BacktestEngineConfig, LoggerConfig
from nautilus_trader.model import BarType

from nautilus_support import TestInstrumentProvider
from strategy_python.trend_following_short_5min import (
    TrendFollowingShort5MinConfig,
    TrendFollowingShort5MinStrategy,
)
from strategy_python.trend_following_short_5min.indicators import HourlyTrendModel, TrendLevels


def test_hourly_individual_predicates_match_existing_default():
    model = HourlyTrendModel(20, 6, use_slope_change=True, max_adverse_ma_return=0.001)
    rng = random.Random(173)
    price = 2000
    for _ in range(1000):
        price += rng.uniform(-15, 15)
        model.update(price)
        assert model.allows_short() == (model.direction == -1)
    model.reset()
    assert not model.allows_short(require_price=False, require_slope=False)


@pytest.mark.parametrize(
    "price,slope,change,remove_price,remove_slope,expected",
    [
        (101, -1, 0, False, False, False),
        (101, -1, 0, True, False, True),
        (101, 1, 0, True, False, False),
        (99, 1, 0, False, True, True),
        (101, 1, 0, False, True, False),
        (99, -1, 0.001, False, True, False),
        (101, -1, 0.001, True, False, False),
        (99, -1, 0.000999, False, False, True),
    ],
)
def test_hourly_removal_preserves_other_two_rules(
    price, slope, change, remove_price, remove_slope, expected
):
    model = HourlyTrendModel(2, 1, use_slope_change=True, max_adverse_ma_return=0.001)
    for close in [100, 100, 100, 100]:
        model.update(close)
    model.last_close = price
    model.last_ma = 100
    model.last_slope = slope
    model.last_ma_return = change
    assert (
        model.allows_short(require_price=not remove_price, require_slope=not remove_slope)
        == expected
    )


@pytest.mark.parametrize(
    "overrides,cross,rearmed,hourly,daily,expected",
    [
        ({}, 0, True, -1, True, 0),
        ({"require_entry_cross": False}, 0, True, -1, True, 1),
        ({"require_entry_cross": False}, 0, False, -1, True, 0),
        ({"require_entry_cross": False}, 0, True, 1, True, 0),
        ({"require_entry_cross": False}, 0, True, -1, False, 0),
        ({}, -1, False, -1, True, 0),
        ({"require_reentry_zone": False}, -1, False, -1, True, 1),
        ({"require_reentry_zone": False}, 0, False, -1, True, 0),
    ],
)
def test_cross_and_reentry_removal_are_independent(
    overrides, cross, rearmed, hourly, daily, expected
):
    instrument = TestInstrumentProvider.ethusdt_perp_binance()

    class Probe(TrendFollowingShort5MinStrategy):
        def cancel_entry(self, state):
            pass

        def _account_equity(self):
            return Decimal(500)

        def submit_entry_market(self, state, quantity):
            self.entries.append(quantity)

    strategy = Probe(
        TrendFollowingShort5MinConfig(
            instrument_id=instrument.id,
            bar_type=BarType.from_str(f"{instrument.id}-1-MINUTE-LAST-EXTERNAL"),
            **(
                {
                    "require_hourly_price_filter": True,
                    "require_hourly_slope_filter": True,
                    "require_reentry_zone": True,
                    "max_adverse_hourly_ma_return": 0.001,
                }
                | overrides
            ),
        )
    )
    engine = BacktestEngine(config=BacktestEngineConfig(logging=LoggerConfig(bypass_logging=True)))
    try:
        engine.add_strategy(strategy)
        strategy.instrument = instrument
        strategy.entries = []
        strategy.daily_boll = SimpleNamespace(allowed=daily)
        strategy.hourly_trend.direction = hourly
        strategy._cross_signal = cross
        strategy.leg.rearmed = rearmed
        strategy._refresh_leg(
            strategy.leg, TrendLevels(2100, 10, 2120, 2080, 2102, 2097, 2098, 2103), 2000
        )
        assert len(strategy.entries) == expected
    finally:
        engine.dispose()
