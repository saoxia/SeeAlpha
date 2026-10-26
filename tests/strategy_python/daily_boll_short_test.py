"""Daily entry filter: live prices, strict comparisons, complete UTC history."""

from decimal import Decimal

import pytest
from nautilus_trader.backtest import BacktestEngine
from nautilus_trader.config import BacktestEngineConfig, LoggerConfig
from nautilus_trader.model import BarType

from nautilus_support import TestInstrumentProvider
from strategy_python.trend_following_short_5min import (
    TrendFollowingShort5MinConfig,
    TrendFollowingShort5MinStrategy,
)
from strategy_python.trend_following_short_5min.indicators import DailyBollEntryFilter, TrendLevels

DAY = DailyBollEntryFilter.DAY_NS
HOUR = DAY // 24


def day(model, number, low=100, close=100, skip=None):
    for hour in range(1, 25):
        if hour != skip:
            model.update(number * DAY + hour * HOUR - 1_000_000, low, close)


def current(model, number, low, close, hour=1):
    model.update(number * DAY + hour * HOUR - 1_000_000, low, close)


def warm(previous_low=90):
    model = DailyBollEntryFilter(period=3, std=2, source_seconds=3600)
    day(model, 0)
    day(model, 1)
    day(model, 2, low=previous_low)
    return model


def test_requires_both_daily_lows_and_strict_comparison():
    model = warm()
    current(model, 3, 100, 100)
    assert not model.allowed  # Equal to lower band is insufficient.
    current(model, 3, 99, 100, hour=2)
    assert model.allowed
    model = warm(previous_low=100)
    current(model, 3, 80, 100)
    assert not model.allowed  # Today's breach cannot replace yesterday's breach.


def test_live_band_recomputed_without_latching_or_future_close():
    model = warm()
    current(model, 3, 90, 100)
    assert model.allowed
    current(model, 3, 80, 80, hour=2)
    assert not model.allowed  # Current lower band falls below the running daily low.
    assert list(model.closes) == [100, 100, 100]


def test_daily_low_is_running_minimum_and_prior_band_uses_prior_close():
    model = warm()
    current(model, 3, 90, 100)
    current(model, 3, 100, 100, hour=2)
    assert model.low == 90 and model.allowed
    # A low below the previous day's initial band but not its final band must fail.
    model = DailyBollEntryFilter(period=3, std=2, source_seconds=3600)
    day(model, 0)
    day(model, 1)
    day(model, 2, low=80, close=80)
    current(model, 3, 50, 100)
    assert not model.previous_below and not model.allowed


def test_warmup_missing_source_bar_missing_day_and_reset():
    model = DailyBollEntryFilter(period=3, source_seconds=3600)
    day(model, 0, low=90)
    day(model, 1, low=90)
    current(model, 2, 90, 100)
    assert not model.allowed
    model = warm()
    current(model, 3, 90, 100)
    assert model.allowed
    current(model, 3, 90, 100, hour=3)  # Missing second hourly source bar.
    assert not model.allowed
    day(model, 4, low=90)
    assert not model.closes and not model.allowed
    model = warm()
    current(model, 4, 90, 100)  # A full missing day must not become yesterday.
    assert not model.allowed and not model.closes
    model = warm()
    model.reset()
    assert not model.allowed and not model.closes


@pytest.mark.parametrize(
    "enabled,ready,expected", [(True, False, 0), (True, True, 1), (False, False, 1)]
)
def test_entry_gate_with_matching_hourly_trend_and_cross(enabled, ready, expected):
    instrument = TestInstrumentProvider.ethusdt_perp_binance()

    class Probe(TrendFollowingShort5MinStrategy):
        def cancel_entry(self, state):
            pass

        def submit_entry_market(self, state, quantity):
            self.entries.append(quantity)

        def _account_equity(self):
            return Decimal(500)

    strategy = Probe(
        TrendFollowingShort5MinConfig(
            instrument_id=instrument.id,
            bar_type=BarType.from_str(f"{instrument.id}-1-MINUTE-LAST-EXTERNAL"),
            daily_boll_entry_filter=enabled,
            require_hourly_price_filter=True,
            require_hourly_slope_filter=True,
        )
    )
    engine = BacktestEngine(config=BacktestEngineConfig(logging=LoggerConfig(bypass_logging=True)))
    try:
        engine.add_strategy(strategy)
        strategy.instrument = instrument
        strategy.entries = []
        if ready:
            strategy.daily_boll = warm()
            current(strategy.daily_boll, 3, 90, 100)
        strategy.hourly_trend.direction = -1
        strategy._cross_signal = -1
        strategy._refresh_leg(
            strategy.leg, TrendLevels(2000, 10, 2020, 1980, 2002, 1997, 1998, 2003), 2000
        )
        assert len(strategy.entries) == expected
    finally:
        engine.dispose()


def conditional_model(closes, *, std=2, limit=0.22, previous_low=None):
    model = DailyBollEntryFilter(
        period=len(closes), std=std, source_seconds=3600, max_width_ratio=limit
    )
    for i, close in enumerate(closes):
        low = previous_low if i == len(closes) - 1 and previous_low is not None else close
        day(model, i, low=low, close=close)
    return model


def test_conditional_filter_blocks_only_narrow_lower_half():
    model = conditional_model([99, 101] * 10)
    current(model, 20, 98, 99)
    assert not model.allowed  # Narrow, inside lower half, previous day did not breach.
    current(model, 20, 98, 101, hour=2)
    assert model.allowed  # Above MA bypasses the two-day low requirement.
    current(model, 20, 80, 80, hour=3)
    assert model.allowed  # Below the lower band also bypasses it.
    current(model, 20, 80, 99, hour=4)
    assert not model.allowed  # Re-entering the zone re-enables the filter immediately.


def test_wide_band_bypasses_even_inside_lower_half():
    model = conditional_model([100, 110, 90, 110])
    current(model, 4, 99, 100)
    assert not model.previous_below
    assert model.allowed  # [110, 90, 110, 100]: width/MA > 22%, price inside lower half.
    model.max_width_ratio = None
    assert not model.allowed  # Previous unconditional behavior remains reproducible.


def test_exact_22_percent_width_is_not_enabled_and_lower_boundary_is_included():
    model = conditional_model([111, 111], std=1)
    current(model, 2, 89, 89)
    assert model.allowed  # [111, 89] gives MA=100, lower=89, upper=111, ratio=22%.
    model.max_width_ratio = 0.220001
    assert not model.allowed  # Price exactly at lower band is inside the activation zone.


def test_ma_boundary_is_included_and_both_lows_still_required():
    model = conditional_model([100] * 20)
    current(model, 20, 90, 100)
    assert not model.allowed  # Price at MA, but previous low equals its lower band.
    model = conditional_model([100] * 20, previous_low=90)
    current(model, 20, 100, 100)
    assert not model.allowed  # Current low equals lower band, not strictly below.
    current(model, 20, 90, 100, hour=2)
    assert model.allowed


def test_conditional_filter_still_blocks_unready_or_incomplete_daily_data():
    model = conditional_model([100] * 20)
    assert not model.allowed  # The 20th completed day is not yet rolled into history.
    current(model, 20, 100, 101)
    assert model.allowed
    current(model, 20, 100, 101, hour=3)
    assert not model.allowed  # Missing hour prevents evaluating today's conditions.


@pytest.mark.parametrize("limit", [0, -0.1, float("nan"), float("inf")])
def test_conditional_width_limit_validation(limit):
    with pytest.raises(ValueError, match="带宽比"):
        DailyBollEntryFilter(max_width_ratio=limit)


def test_upper_band_gate_narrow_allowed_wide_blocked_and_legacy_toggle():
    model = conditional_model([100] * 20)
    model.upper_max_width_ratio = 0.22
    current(model, 20, 100, 105)
    assert model.allowed  # Above upper band, narrow bandwidth, no two-low requirement.
    current(model, 20, 100, 150, hour=2)
    assert not model.allowed  # Above upper band with bandwidth > 22%.
    model.upper_max_width_ratio = None
    assert model.allowed  # Previous conditional version is reproducible.
    model.upper_max_width_ratio = 0.22
    current(model, 20, 100, 105, hour=3)
    assert model.allowed  # Gate follows the current dynamic band, not a latched breach.


def test_upper_band_gate_includes_price_boundary_and_rejects_exact_width():
    model = conditional_model([89, 89], std=1)
    model.upper_max_width_ratio = 0.22
    current(model, 2, 100, 111)
    assert not model.allowed  # MA=100, upper=111, lower=89; width is exactly 22%.
    model.upper_max_width_ratio = 0.220001
    assert model.allowed


def test_upper_gate_does_not_block_wide_band_below_upper_or_bypass_lower_filter():
    model = conditional_model([100, 110, 90, 110])
    model.upper_max_width_ratio = 0.22
    current(model, 4, 105, 105)
    assert model.allowed  # Between MA and upper band, wide bandwidth.
    model = conditional_model([99, 101] * 10)
    model.upper_max_width_ratio = 0.22
    current(model, 20, 98, 99)
    assert not model.allowed  # Existing narrow lower-half rule still requires yesterday.


@pytest.mark.parametrize("limit", [0, -0.1, float("nan"), float("inf")])
def test_upper_width_limit_validation(limit):
    with pytest.raises(ValueError, match="上轨带宽比"):
        DailyBollEntryFilter(upper_max_width_ratio=limit)


@pytest.mark.parametrize(
    "allowed,trade_start,expected", [(True, 0, 1), (False, 0, 0), (True, DAY, 0)]
)
def test_daily_only_ignores_cross_hourly_and_rearm_but_requires_daily_gate(
    allowed, trade_start, expected
):
    from nautilus_trader.model import Bar

    instrument = TestInstrumentProvider.ethusdt_perp_binance()
    bar_type = BarType.from_str(f"{instrument.id}-1-MINUTE-LAST-EXTERNAL")

    class Probe(TrendFollowingShort5MinStrategy):
        def _account_equity(self):
            return Decimal(500)

        def _update_levels(self, *args):
            raise AssertionError("Daily-only entries must not depend on 5-minute MA indicators")

        def _update_peak_exits(self, bar):
            self.peak_updates += 1

        def submit_entry_market(self, state, quantity):
            self.entries.append(quantity)
            state.record_entry_fill(quantity, Decimal(100))

    class DailyGate:
        def update(self, *args):
            pass

    strategy = Probe(
        TrendFollowingShort5MinConfig(
            instrument_id=instrument.id,
            bar_type=bar_type,
            daily_boll_only_entry=True,
            trade_start_ns=trade_start,
        )
    )
    engine = BacktestEngine(config=BacktestEngineConfig(logging=LoggerConfig(bypass_logging=True)))
    try:
        engine.add_strategy(strategy)
        strategy.instrument = instrument
        strategy.entries = []
        strategy.peak_updates = 0
        strategy.daily_boll = DailyGate()
        strategy.daily_boll.allowed = allowed
        strategy.leg.rearmed = False
        strategy.leg.stop_cooldown_until_ns = DAY
        strategy.hourly_trend.direction = 1
        strategy._cross_signal = 0
        price = instrument.make_price(100)
        for minute in range(1, 11):
            ts = minute * 60_000_000_000 - 1_000_000
            strategy.on_bar(
                Bar(bar_type, price, price, price, price, instrument.make_qty(1), ts, ts)
            )
            if minute < 5:
                assert not strategy.entries
        assert len(strategy.entries) == expected
        assert strategy.peak_updates == 10
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    "overrides",
    [
        {"daily_boll_entry_filter": False},
        {"daily_boll_max_width_ratio": 0.22},
        {"daily_boll_upper_max_width_ratio": 0.22},
        {"peak_profit_exit": False},
        {"max_positions_per_side": 2},
    ],
)
def test_daily_only_rejects_incompatible_modes(overrides):
    instrument = TestInstrumentProvider.ethusdt_perp_binance()
    with pytest.raises(ValueError, match="仅日线 BOLL"):
        TrendFollowingShort5MinStrategy(
            TrendFollowingShort5MinConfig(
                instrument_id=instrument.id,
                bar_type=BarType.from_str(f"{instrument.id}-1-MINUTE-LAST-EXTERNAL"),
                daily_boll_only_entry=True,
                **overrides,
            )
        )
