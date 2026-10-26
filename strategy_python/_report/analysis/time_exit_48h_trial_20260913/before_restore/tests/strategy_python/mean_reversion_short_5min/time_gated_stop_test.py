from decimal import Decimal

import pytest
from nautilus_trader.test_kit.providers import TestInstrumentProvider

from strategy_python.mean_reversion_short_5min import MeanReversionShort5MinStrategy
from tests.strategy_python.mean_reversion_short_5min.strategy_test import make_config, MINUTE_BAR_TYPE

DAY = 86_400_000_000_000


@pytest.mark.parametrize('elapsed,loss', [
    (DAY, None), (2*DAY-1, None), (2*DAY, '.01'), (3*DAY, '.02'),
    (5*DAY-1, '.02'), (5*DAY, '.04'), (8*DAY, '.07'),
    (12*DAY, '.11'), (17*DAY, '.16'), (23*DAY, '.22'),
    (30*DAY-1, '.22'), (30*DAY, '.25'), (400*DAY, '.25'),
])
def test_trial_boundary_and_daily_allowance(elapsed, loss):
    strategy = MeanReversionShort5MinStrategy(make_config(
        time_gated_stop_trial=True, time_exit_max_loss=.25))
    state = strategy.trade_state
    state.quantity, state.average_price = Decimal(2), Decimal(100)
    state.last_add_fill_ns = 100
    assert strategy._activate_breakeven_exit(state, 100+elapsed) is (loss is not None)
    if loss is not None:
        price = Decimal(100)*(1+Decimal(loss))
        assert strategy._take_profit_prices(state) == (price, price)


@pytest.mark.parametrize('phase', ['initial', 'scaled'])
def test_trial_never_submits_fixed_stop_even_above_old_stop(phase):
    calls = []

    class Observe(MeanReversionShort5MinStrategy):
        def _upsert_exit(self, state, role, quantity, price, full_exit=False):
            calls.append((role, price))

        def _submit_emergency_exit(self, state, reason):
            pytest.fail(f'Unexpected market exit: {reason}')

    strategy = Observe(make_config(time_gated_stop_trial=True, time_exit_max_loss=.25))
    strategy.instrument = TestInstrumentProvider.ethusdt_perp_binance()
    state = strategy.trade_state
    state.phase, state.position_id = phase, 'test'
    state.quantity, state.average_price = Decimal(2), Decimal(100)
    state.last_add_fill_ns = 100
    strategy._observe_market_price(Decimal(150), 100+2*DAY)
    strategy._activate_breakeven_exit(state, 100+2*DAY)
    strategy._refresh_protection(state)
    assert calls and all(role != 'stop' and price == Decimal(101) for role, price in calls)
    assert not strategy._certain_initial_fill_then_stop(state, None)


def test_native_trial_survives_old_stop_and_only_fills_inside_allowed_loss():
    from nautilus_trader.backtest.engine import BacktestEngine
    from nautilus_trader.config import BacktestEngineConfig, LoggingConfig
    from nautilus_trader.model.data import Bar
    from nautilus_trader.model.enums import AccountType, OmsType
    from nautilus_trader.model.objects import Money
    from strategy_python.mean_reversion_short_5min.position import EntryTarget

    instrument = TestInstrumentProvider.ethusdt_perp_binance()
    snapshots = []

    class Scenario(MeanReversionShort5MinStrategy):
        def on_start(self):
            super().on_start()
            self.features.bollinger = (95., 100., 105.)
            self.features.min_bollinger_width = 20.
            self.features.ma_distance.current_ma = 95.
            self.features.take_profit_distance_mean = -5.
            self.features.add_distance_mean = 10.
            self.apply_entry_target(self.trade_state, EntryTarget(
                direction=-1, role='initial', quantity=Decimal(1), price=Decimal(100),
                planned_risk=Decimal(500)))

        def on_bar(self, bar):
            super().on_bar(bar)
            snapshots.append((self.trade_state.quantity, self.trade_state.breakeven_exit_active))

    strategy = Scenario(make_config(close_positions_on_stop=False, time_gated_stop_trial=True, time_exit_max_loss=.25))
    engine = BacktestEngine(config=BacktestEngineConfig(logging=LoggingConfig(bypass_logging=True)))
    try:
        engine.add_venue(venue=instrument.id.venue, oms_type=OmsType.HEDGING,
                         account_type=AccountType.MARGIN, base_currency=None,
                         starting_balances=[Money(1000, instrument.quote_currency)],
                         default_leverage=Decimal(5))
        engine.add_instrument(instrument)
        base = 1785542400000000000
        minute = 60_000_000_000
        times_prices = [(minute, 100), (2*minute, 110), (2*minute+DAY, 150),
                        (2*minute+2*DAY, 150), (3*minute+2*DAY, 140),
                        (4*minute+2*DAY, 103.53)]
        bars = []
        for offset, value in times_prices:
            ts = base+offset-1_000_000
            px = instrument.make_price(value)
            bars.append(Bar(MINUTE_BAR_TYPE, px, px, px, px, instrument.make_qty(1000), ts, ts))
        engine.add_data(bars)
        engine.add_strategy(strategy)
        engine.run()
        assert snapshots[2] == (Decimal(2), False)
        assert snapshots[3] == (Decimal(2), True)  # No market liquidation at 110.
        assert snapshots[4] == (Decimal(2), True)
        assert snapshots[-1][0] == 0
        assert not engine.cache.positions_open()
        profits = [o for o in engine.cache.orders() if o.filled_qty.as_decimal() > 0
                   and any(':take-profit:' in tag for tag in o.tags or [])]
        assert len(profits) == 1 and profits[0].filled_qty.as_decimal() == Decimal(2)
        assert profits[0].price.as_decimal() == Decimal('103.53')
        assert profits[0].order_type.name == 'LIMIT'
        assert all(o.order_type.name != 'STOP_MARKET' for o in engine.cache.orders())
        assert not strategy.trade_state.reentry_waiting
        assert strategy.trade_state.hard_stop_count == 0
    finally:
        engine.dispose()


def test_trial_initial_timer_and_add_restart():
    strategy = MeanReversionShort5MinStrategy(make_config(
        time_gated_stop_trial=True, time_exit_max_loss=.25))
    state = strategy.trade_state
    state.quantity, state.average_price = Decimal(1), Decimal(100)
    state.position_start_ns = 100
    assert not strategy._breakeven_exit_due(state, 100+2*DAY-1)
    assert strategy._breakeven_exit_due(state, 100+2*DAY)
    state.last_add_fill_ns = 100+DAY
    assert not strategy._breakeven_exit_due(state, 100+2*DAY)
    assert strategy._activate_breakeven_exit(state, 100+3*DAY)
    assert state.time_exit_loss_fraction == Decimal('.01')
    state.clear_cycle()
    assert strategy._time_exit_origin(state) is None
