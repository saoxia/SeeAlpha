from decimal import Decimal
from types import SimpleNamespace

import pytest
from nautilus_trader.test_kit.providers import TestInstrumentProvider

from strategy_python.mean_reversion_short_5min import MeanReversionShort5MinStrategy
from tests.strategy_python.mean_reversion_short_5min.strategy_test import make_config, MINUTE_BAR_TYPE

DAY = 24 * 60 * 60 * 1_000_000_000


@pytest.mark.parametrize('offset,expected', [(DAY-1, False), (DAY, False), (DAY+1, True)])
def test_breakeven_requires_strictly_more_than_24_hours_after_latest_add(offset, expected):
    strategy = MeanReversionShort5MinStrategy(make_config())
    state = strategy.trade_state
    state.quantity, state.average_price = Decimal(2), Decimal(100)
    state.last_initial_fill_ns = 0
    assert not strategy._breakeven_exit_due(state, 3*DAY)  # Initial-only position.
    state.last_add_fill_ns = 100
    assert strategy._breakeven_exit_due(state, 100+offset) is expected
    state.last_add_fill_ns = 100+DAY  # Later add restarts the wait.
    assert not strategy._breakeven_exit_due(state, 100+offset)


def test_breakeven_activation_cancels_add_and_targets_all_at_average_without_indicators():
    from strategy_python.mean_reversion_short_5min.execution import OrderMeta

    calls, canceled = {}, []

    class Observe(MeanReversionShort5MinStrategy):
        def _cancel_entry(self, state):
            canceled.append(state.entry_order_id)

        def _upsert_exit(self, state, role, quantity, price, full_exit=False):
            calls[role] = (quantity, price, full_exit)

    strategy = Observe(make_config())
    strategy.instrument = TestInstrumentProvider.ethusdt_perp_binance()
    state = strategy.trade_state
    state.phase, state.position_id = 'scaled', 'test'
    state.quantity, state.unit_quantity, state.average_price = Decimal(2), Decimal(1), Decimal(100)
    state.last_add_fill_ns, state.entry_order_id = 1, 'add'
    strategy._order_meta['add'] = OrderMeta(-1, 'add', Decimal(1))
    assert strategy._activate_breakeven_exit(state, DAY+2)
    assert state.exit_started and strategy._order_meta['add'].invalidated
    strategy._refresh_protection(state)
    assert calls['take_profit_second'] == (Decimal(2), Decimal(100), True)
    assert calls['stop'][1] == Decimal(130)
    assert 'take_profit' not in calls
    assert canceled == ['add']
    assert not strategy._activate_breakeven_exit(state, DAY+3)
    assert not state.reentry_waiting
    state.clear_cycle()
    assert state.last_add_fill_ns is None and not state.breakeven_exit_active


def test_partial_add_fills_update_timer_but_reductions_do_not():
    from nautilus_trader.model.objects import Money
    from strategy_python.mean_reversion_short_5min.execution import OrderMeta

    instrument = TestInstrumentProvider.ethusdt_perp_binance()
    order = SimpleNamespace(is_closed=False)

    class Observe(MeanReversionShort5MinStrategy):
        def _strategy(self):
            return SimpleNamespace(cache=SimpleNamespace(order=lambda key: order))

        def _refresh_protection(self, state):
            pass

        def _refresh_add_order(self, state, now):
            pass

    strategy = Observe(make_config())
    strategy.instrument = instrument
    state = strategy.trade_state
    state.quantity, state.unit_quantity, state.average_price = Decimal(1), Decimal(1), Decimal(100)
    strategy._order_meta['add'] = OrderMeta(-1, 'add', Decimal(1))
    for now, quantity in [(10, '.3'), (20, '.7')]:
        strategy.on_order_filled(SimpleNamespace(
            client_order_id='add', ts_event=now, position_id='test',
            last_qty=instrument.make_qty(Decimal(quantity)), last_px=instrument.make_price(110),
            commission=Money(0, instrument.quote_currency)))
        assert state.last_add_fill_ns == now
    state.apply_exit(Decimal('1.5'))
    assert state.last_add_fill_ns == 20


@pytest.mark.parametrize('aged', [False, True])
def test_native_timeout_waits_at_average_then_exits_by_limit_without_stop_recovery(aged):
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

    strategy = Scenario(make_config(close_positions_on_stop=False))
    engine = BacktestEngine(config=BacktestEngineConfig(logging=LoggingConfig(bypass_logging=True)))
    try:
        engine.add_venue(venue=instrument.id.venue, oms_type=OmsType.HEDGING,
                         account_type=AccountType.MARGIN, base_currency=None,
                         starting_balances=[Money(1000, instrument.quote_currency)],
                         default_leverage=Decimal(5))
        engine.add_instrument(instrument)
        base = 1785542400000000000
        minute = 60_000_000_000
        times_prices = [(minute, 100), (2*minute, 109), (2*minute+DAY, 110),
                        (3*minute+DAY, 110), (4*minute+DAY, 102.5)]
        if aged:
            times_prices = times_prices[:-1] + [(2*minute+2*DAY, 110), (3*minute+2*DAY, 103.53)]
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
        assert snapshots[-1][0] == 0
        assert not engine.cache.positions_open()
        profits = [o for o in engine.cache.orders() if o.filled_qty.as_decimal() > 0
                   and any(':take-profit:' in tag for tag in o.tags or [])]
        assert len(profits) == 1 and profits[0].filled_qty.as_decimal() == Decimal(2)
        assert profits[0].price.as_decimal() == Decimal('103.53' if aged else '102.5')
        assert profits[0].order_type.name == 'LIMIT'
        assert not strategy.trade_state.reentry_waiting
        assert strategy.trade_state.hard_stop_count == 0
    finally:
        engine.dispose()


@pytest.mark.parametrize('elapsed,expected', [
    (DAY+1, '0'), (2*DAY-1, '0'), (2*DAY, '.01'),
    (3*DAY, '.02'), (6*DAY, '.05'), (100*DAY, '.05'),
])
def test_time_exit_loss_schedule_and_cap(elapsed, expected):
    strategy = MeanReversionShort5MinStrategy(make_config())
    state = strategy.trade_state
    state.quantity, state.average_price = Decimal(2), Decimal(100)
    state.last_add_fill_ns = 10
    assert strategy._activate_breakeven_exit(state, 10+elapsed)
    assert state.time_exit_loss_fraction == Decimal(expected)
    price = Decimal(100)*(1+Decimal(expected))
    assert strategy._take_profit_prices(state) == (price, price)
    assert not strategy._activate_breakeven_exit(state, 10+elapsed)
    state.clear_cycle()
    assert state.time_exit_loss_fraction == 0


def test_zero_step_reproduces_fixed_breakeven_baseline():
    strategy = MeanReversionShort5MinStrategy(make_config(time_exit_loss_step=0))
    state = strategy.trade_state
    state.quantity, state.average_price = Decimal(2), Decimal(100)
    state.last_add_fill_ns = 1
    assert strategy._activate_breakeven_exit(state, 100*DAY)
    assert strategy._take_profit_prices(state) == (Decimal(100), Decimal(100))


@pytest.mark.parametrize('field', ['time_exit_loss_step', 'time_exit_max_loss'])
@pytest.mark.parametrize('value', [-.01, 1., float('nan'), float('inf')])
def test_invalid_time_exit_loss_parameters_rejected(field, value):
    with pytest.raises(ValueError, match='超时退出亏损比例'):
        MeanReversionShort5MinStrategy(make_config(**{field: value}))
