from decimal import Decimal

import pytest

from nautilus_support import instrument_fee_model
from strategy_python.mean_reversion_short_5min import MeanReversionShort5MinStrategy
from tests.strategy_python.mean_reversion_short_5min.strategy_test import completed_bar, make_config

STEP = 300_000_000_000
BANDS = (95., 100., 105.)


def setup_strategy():
    strategy = MeanReversionShort5MinStrategy(make_config())
    state = strategy.trade_state
    state.quantity, state.average_price = Decimal(2), Decimal(100)
    strategy.features.ma_distance.current_ma = 95.
    state.position_start_ns = 0
    return strategy, state


def test_48_complete_candles_required_and_duplicate_ignored():
    strategy, state = setup_strategy()
    for n in range(1, 48):
        assert not strategy._no_lower_touch_stop_due(state, completed_bar(n*STEP, low=96), BANDS)
    assert state.no_lower_touch_count == 47
    assert not strategy._no_lower_touch_stop_due(state, completed_bar(47*STEP, low=96), BANDS)
    assert strategy._no_lower_touch_stop_due(state, completed_bar(48*STEP, low=96), BANDS)
    state.clear_cycle()
    assert state.no_lower_touch_count == 0 and state.no_lower_touch_last_ns is None


@pytest.mark.parametrize('low,bands', [(95., BANDS), (94., BANDS), (96., None), (float('nan'), BANDS)])
def test_touch_or_missing_data_breaks_streak(low, bands):
    strategy, state = setup_strategy()
    state.no_lower_touch_count, state.no_lower_touch_last_ns = 47, 47*STEP
    assert not strategy._no_lower_touch_stop_due(state, completed_bar(48*STEP, low=low), bands)
    assert state.no_lower_touch_count == 0


def test_gap_and_entry_candle_excluded_add_does_not_reset():
    strategy, state = setup_strategy()
    state.position_start_ns = 1
    assert not strategy._no_lower_touch_stop_due(state, completed_bar(STEP, low=96), BANDS)
    assert state.no_lower_touch_count == 0
    state.no_lower_touch_count, state.no_lower_touch_last_ns = 47, 47*STEP
    assert not strategy._no_lower_touch_stop_due(state, completed_bar(49*STEP, low=96), BANDS)
    assert state.no_lower_touch_count == 1
    state.apply_entry(Decimal(1), Decimal(101), Decimal(0))
    state.last_add_fill_ns = 49*STEP
    assert not strategy._no_lower_touch_stop_due(state, completed_bar(50*STEP, low=96), BANDS)
    assert state.no_lower_touch_count == 2


@pytest.mark.parametrize('wait_for_price', [False, True])
def test_native_no_touch_stop_closes_all_and_enters_recovery(wait_for_price):
    from nautilus_trader.backtest import BacktestEngine
    from nautilus_trader.config import BacktestEngineConfig, LoggerConfig
    from nautilus_trader.model import AccountType, Bar, Money, OmsType

    from nautilus_support import TestInstrumentProvider
    from strategy_python.mean_reversion_short_5min.position import EntryTarget
    from tests.strategy_python.mean_reversion_short_5min.strategy_test import MINUTE_BAR_TYPE

    instrument = TestInstrumentProvider.ethusdt_perp_binance()
    snapshots = []

    class Scenario(MeanReversionShort5MinStrategy):
        def on_start(self):
            super().on_start()
            self.features.bollinger = BANDS
            self.features.ma_distance.current_ma = 99.
            self.features.min_bollinger_width = 0.
            self.features.on_five_minute_bar = lambda bar: BANDS
            self.apply_entry_target(self.trade_state, EntryTarget(
                direction=-1, role='initial', quantity=Decimal(1), price=Decimal(100),
                planned_risk=Decimal(100)))

        def _refresh_add_order(self, state, now_ns):
            pass

        def on_bar(self, bar):
            super().on_bar(bar)
            snapshots.append(self.trade_state.quantity)

    strategy = Scenario(make_config(close_positions_on_stop=False))
    engine = BacktestEngine(config=BacktestEngineConfig(logging=LoggerConfig(bypass_logging=True)))
    try:
        engine.add_venue(venue=instrument.id.venue, oms_type=OmsType.HEDGING,
                         account_type=AccountType.MARGIN, base_currency=None,
                             fee_model=instrument_fee_model(instrument),
                         starting_balances=[Money(1000, instrument.quote_currency)], default_leverage=Decimal(5))
        engine.add_instrument(instrument)
        px = instrument.make_price(100)
        bars = []
        base = 1785542400000000000
        for i in range(247 if wait_for_price else 246):
            px = instrument.make_price(100 if not wait_for_price or i == 0 else 101 if i == 246 else 103)
            ts = base + (i+1)*60_000_000_000-1_000_000
            bars.append(Bar(MINUTE_BAR_TYPE, px, px, px, px, instrument.make_qty(1000), ts, ts))
        engine.add_data(bars)
        engine.add_strategy(strategy)
        engine.run()
        assert snapshots[-2] == 1 and snapshots[-1] == 0
        if wait_for_price:
            assert snapshots[245] == 1  # Activated at 103, above the 101 limit.
        exits = [o for o in engine.cache.orders() if o.order_type.name == 'LIMIT' and o.is_reduce_only and o.filled_qty.as_decimal() > 0]
        assert len(exits) == 1 and exits[0].price.as_decimal() == Decimal(101)
        assert exits[0].filled_qty.as_decimal() == 1 and exits[0].is_reduce_only
        assert not engine.cache.positions_open()
        assert strategy.trade_state.reentry_waiting and strategy.trade_state.hard_stop_count == 1
    finally:
        engine.dispose()


@pytest.mark.parametrize('ma99', [96., 97., None, float('nan'), float('inf')])
def test_ma_touch_cross_or_missing_breaks_streak_despite_close_above(ma99):
    strategy, state = setup_strategy()
    state.no_lower_touch_count, state.no_lower_touch_last_ns = 47, 47*STEP
    strategy.features.ma_distance.current_ma = ma99
    assert not strategy._no_lower_touch_stop_due(
        state, completed_bar(48*STEP, low=96, close=100), BANDS)
    assert state.no_lower_touch_count == 0
    strategy.features.ma_distance.current_ma = 95.
    for n in range(49, 96):
        assert not strategy._no_lower_touch_stop_due(state, completed_bar(n*STEP, low=96), BANDS)
    assert strategy._no_lower_touch_stop_due(state, completed_bar(96*STEP, low=96), BANDS)


def test_latched_limit_overrides_time_exit_and_survives_indicator_change():
    from nautilus_support import TestInstrumentProvider
    strategy, state = setup_strategy()
    strategy.instrument = TestInstrumentProvider.ethusdt_perp_binance()
    state.no_lower_touch_exit_active = True
    state.breakeven_exit_active = True
    state.time_exit_loss_fraction = Decimal('.05')
    state.average_price = Decimal('100.005')
    strategy.features.ma_distance.current_ma = None
    assert not strategy._no_lower_touch_stop_due(state, completed_bar(STEP, low=90), None)
    assert strategy._take_profit_prices(state) == (Decimal(101), Decimal(101))
    state.clear_cycle()
    assert not state.no_lower_touch_exit_active
