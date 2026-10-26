from decimal import Decimal

import pytest
from nautilus_trader.backtest import BacktestEngine
from nautilus_trader.config import BacktestEngineConfig, LoggerConfig
from nautilus_trader.model import AccountType, Bar, Money, OmsType

from nautilus_support import TestInstrumentProvider, instrument_fee_model
from strategy_python.mean_reversion_short_5min import MeanReversionShort5MinStrategy
from strategy_python.mean_reversion_short_5min.position import EntryTarget
from tests.strategy_python.mean_reversion_short_5min.strategy_test import (
    MINUTE_BAR_TYPE,
    make_config,
)


@pytest.mark.parametrize('mode', ['remainder_profit', 'gap', 'same_price', 'single', 'remainder_stop', 'general_stop', 'protection_crossed', 'second_first'])
def test_native_two_profit_orders_and_refill(mode):
    instrument = TestInstrumentProvider.ethusdt_perp_binance()
    snapshots = []
    phases = []
    stop_targets = []
    rejections = []

    class Scenario(MeanReversionShort5MinStrategy):
        def on_start(self):
            super().on_start()
            self.features.min_bollinger_width = 20. if mode == 'second_first' else 0.
            self.features.bollinger = (90. if mode == 'same_price' else 95., 100., 105.)
            self.features.ma_distance.current_ma = 95.
            self.features.stop_ma_distance.current_ma = 95.
            self.features.take_profit_distance_mean = -5.
            self.features.add_distance_mean = 10.
            self.features.stop_ma_distance.current_ma = 200.
            self.features.stop_p995 = 5.
            self.apply_entry_target(self.trade_state, EntryTarget(
                direction=-1, role='initial', quantity=Decimal(1), price=Decimal(100),
                planned_risk=Decimal(500)))

        def on_order_modify_rejected(self, event):
            rejections.append(event.reason)
            super().on_order_modify_rejected(event)

        def on_bar(self, bar):
            # Native order matching happens before this snapshot; no auto new cycles.
            snapshots.append((self.trade_state.quantity, self.trade_state.average_price))
            phases.append(self.trade_state.phase)
            stop_targets.append(self._stop_target_price(self.trade_state))
            if mode == 'protection_crossed' and len(snapshots) == 2:
                self.features.bollinger = (108., 115., 122.)
                self._refresh_protection(self.trade_state)

    strategy = Scenario(make_config(close_positions_on_stop=False, max_units=1 if mode == 'single' else 2))
    engine = BacktestEngine(config=BacktestEngineConfig(logging=LoggerConfig(bypass_logging=True)))
    try:
        engine.add_venue(venue=instrument.id.venue, oms_type=OmsType.HEDGING,
                         account_type=AccountType.MARGIN, base_currency=None,
                         starting_balances=[Money(1000, instrument.quote_currency)],
                             fee_model=instrument_fee_model(instrument),
                         default_leverage=Decimal(5))
        engine.add_instrument(instrument)
        values = {'remainder_profit': [100, 109, 95, 89], 'gap': [100, 109, 89],
                  'same_price': [100, 109, 90], 'single': [100, 95, 89], 'remainder_stop': [100, 109, 95, 102], 'general_stop': [100, 109, 206], 'protection_crossed': [100, 109, 105], 'second_first': [100, 109, 90, 85]}[mode]
        bars = []
        for i, value in enumerate(values):
            ts = 1785542400000000000 + (i + 1) * 60_000_000_000
            px = instrument.make_price(value)
            bars.append(Bar(MINUTE_BAR_TYPE, px, px, px, px, instrument.make_qty(1000), ts, ts))
        engine.add_data(bars)
        engine.add_strategy(strategy)
        engine.run()
        orders = engine.cache.orders()
        adds = [o for o in orders if any(':add:' in tag for tag in o.tags or []) and o.filled_qty.as_decimal() > 0]
        profits = [o for o in orders if any(':take-profit:' in tag for tag in o.tags or [])]
        assert not engine.cache.positions_open()
        assert len(adds) == (0 if mode == 'single' else 1)
        assert sum(o.filled_qty.as_decimal() for o in profits) == (Decimal(2) if mode == 'second_first' else 0 if mode == 'general_stop' else 2 if mode == 'remainder_profit' else Decimal('1.5') if mode in ('remainder_stop', 'protection_crossed') else 1 if mode == 'single' else 2)
        assert all(o.quantity.as_decimal() <= Decimal(2) for o in profits), [(o.tags, str(o.quantity), str(o.filled_qty), str(o.status)) for o in profits]
        assert all(quantity <= 2 for quantity, _ in snapshots)
        if mode == 'remainder_profit':
            assert phases[:3] == ['initial', 'scaled', 'remainder']
            assert snapshots[2][0] == Decimal(".5")
            assert stop_targets[2] == Decimal('101.25')
            filled = [o for o in profits if o.filled_qty.as_decimal() > 0]
            assert sorted(o.price.as_decimal() for o in filled) == [Decimal(90), Decimal(95)]
        elif mode in ('remainder_stop', 'general_stop'):
            stops = [o for o in orders if any(':hard-stop:' in tag for tag in o.tags or [])
                     and o.filled_qty.as_decimal() > 0]
            requires_wait = mode == 'general_stop'
            assert len(stops) == 1
            assert stops[0].filled_qty.as_decimal() == (2 if requires_wait else Decimal(".5"))
            # The add fills at its 105 limit, so the two-unit average is 102.5.
            assert stops[0].trigger_price.as_decimal() == (Decimal(164) if requires_wait else Decimal('101.25'))
            assert strategy.trade_state.reentry_waiting is requires_wait
            assert strategy.trade_state.hard_stop_count == int(requires_wait)
        elif mode == 'protection_crossed':
            triggered = [o for o in orders if any('triggered-exit:remainder_protection_triggered' in tag
                                                  for tag in o.tags or [])]
            assert len(triggered) == 1 and triggered[0].filled_qty.as_decimal() == Decimal('.5')
            assert triggered[0].avg_px == 105.
            assert not rejections
            assert not strategy.trade_state.reentry_waiting
            assert not any(':emergency:' in tag for o in orders for tag in o.tags or [])
        elif mode == 'second_first':
            filled = [o for o in profits if o.filled_qty.as_decimal() > 0]
            assert len(filled) == 1 and filled[0].filled_qty.as_decimal() == Decimal(2)
            assert filled[0].price.as_decimal() == Decimal(85)
            assert snapshots[2][0] == Decimal(2)  # Touching P2=90 does not exit before P1=85.
            assert filled[0].order_type.name == 'LIMIT'
            assert not strategy.trade_state.reentry_waiting
            assert strategy.trade_state.hard_stop_count == 0
            assert not any(o.order_type.name == 'MARKET' for o in orders)
            assert not rejections
        elif mode == 'same_price':
            assert all(o.price.as_decimal() == 90 for o in profits)
        elif mode == 'single':
            assert snapshots[1][0] == 0  # Initial one-unit position exits at BOLL lower.
    finally:
        engine.dispose()


def test_partial_profit_fill_finishes_one_unit_before_refilling():
    from types import SimpleNamespace

    from strategy_python.mean_reversion_short_5min.execution import OrderMeta

    instrument = TestInstrumentProvider.ethusdt_perp_binance()
    calls, adds = {}, []
    orders = {
        'first': SimpleNamespace(is_closed=False, quantity=instrument.make_qty(1.5),
                                 filled_qty=instrument.make_qty(.4), price=instrument.make_price(95)),
        'second': SimpleNamespace(is_closed=False, quantity=instrument.make_qty(.5),
                                  filled_qty=instrument.make_qty(0), price=instrument.make_price(90)),
    }

    class Observe(MeanReversionShort5MinStrategy):
        def _strategy(self):
            return SimpleNamespace(cache=SimpleNamespace(order=lambda key: orders.get(key)))

        def _cancel_entry(self, state):
            pass

        def _upsert_exit(self, state, role, quantity, price, full_exit=False):
            calls[role] = quantity

        def apply_entry_target(self, state, target):
            adds.append(target)

    strategy = Observe(make_config())
    strategy.instrument = instrument
    state = strategy.trade_state
    state.quantity, state.unit_quantity = Decimal(2), Decimal(1)
    state.phase = 'scaled'
    state.average_price, state.entry_ma99 = Decimal(105), Decimal(95)
    state.position_id = 'test'
    state.cycle_risk_budget = Decimal(500)
    state.take_profit_order_id, state.second_take_profit_order_id = 'first', 'second'
    for key, role in [('first', 'take_profit'), ('second', 'take_profit_second')]:
        strategy._order_meta[key] = OrderMeta(-1, role, Decimal("1.5") if key == "first" else Decimal(".5"))
    features = strategy.features
    state.last_entry_price = Decimal(100)
    features.min_bollinger_width = 0.
    features.bollinger = (95., 100., 105.)
    features.ma_distance.current_ma = 95.
    features.take_profit_distance_mean = -5.
    features.add_distance_mean = 10.
    features.stop_ma_distance.current_ma, features.stop_p995 = 200., 5.

    def fill(quantity):
        strategy.on_order_filled(SimpleNamespace(
            client_order_id='first', ts_event=1, position_id='test',
            last_qty=instrument.make_qty(quantity), last_px=instrument.make_price(95),
            commission=Money(0, instrument.quote_currency)))

    fill(.4)
    assert state.add_distance_snapshot == Decimal(10)
    features.add_distance_mean = 20.
    assert state.quantity == Decimal('1.6') and not state.exit_started
    assert calls['take_profit'] == Decimal('1.1') and calls['take_profit_second'] == Decimal('.5')
    assert state.phase == 'scaled'
    assert not adds
    orders['first'].is_closed = True
    orders['first'].filled_qty = instrument.make_qty(1.5)
    fill(1.1)
    assert state.add_distance_snapshot == Decimal(20)
    assert state.quantity == Decimal(".5") and not state.exit_started
    assert state.phase == 'remainder'
    assert len(adds) == 1 and adds[0].price == Decimal(115) and adds[0].quantity == Decimal("1.5")

    # A partial refill from 0.5 to 0.7 units already restores scaled protection;
    # the outstanding add must still request the entire 1.3-unit gap.
    orders['add'] = SimpleNamespace(is_closed=False)
    state.entry_order_id = 'add'
    strategy._order_meta['add'] = OrderMeta(-1, 'add', Decimal('1.5'))
    strategy.on_order_filled(SimpleNamespace(
        client_order_id='add', ts_event=2, position_id='test',
        last_qty=instrument.make_qty(.2), last_px=instrument.make_price(115),
        commission=Money(0, instrument.quote_currency)))
    assert state.quantity == Decimal('.7') and state.phase == 'scaled'
    assert calls['take_profit'] == Decimal('.2')
    assert calls['take_profit_second'] == Decimal('.5')
    assert adds[-1].quantity == Decimal('1.3')
