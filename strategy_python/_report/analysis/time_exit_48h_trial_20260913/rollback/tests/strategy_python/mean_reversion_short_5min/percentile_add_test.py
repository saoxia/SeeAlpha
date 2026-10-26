"""锁定第二份挂单，并通过原生成交引擎检查两种成交先后顺序。"""
from decimal import Decimal

import pytest
from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.config import BacktestEngineConfig, LoggingConfig
from nautilus_trader.model.data import Bar
from nautilus_trader.model.enums import AccountType, OmsType
from nautilus_trader.model.objects import Money
from nautilus_trader.test_kit.providers import TestInstrumentProvider

from strategy_python.mean_reversion_short_5min import MeanReversionShort5MinStrategy
from strategy_python.mean_reversion_short_5min.position import EntryTarget
from tests.strategy_python.mean_reversion_short_5min.strategy_test import (
    MINUTE_BAR_TYPE,
    make_config,
)


@pytest.mark.parametrize('add_first', [False, True])
def test_native_dynamic_add_and_take_profit_cancel_sibling(add_first):
    instrument = TestInstrumentProvider.ethusdt_perp_binance()
    snapshots = []

    class Scenario(MeanReversionShort5MinStrategy):
        step = 0

        def on_start(self):
            super().on_start()
            self.features.min_bollinger_width = 0.
            self.features.bollinger = (90.0, 95.0, 100.0)
            self.features.stop_ma_distance.current_ma = 140.
            self.features.stop_p995 = 40.
            self.features.add_distance_mean = 7.
            self.features.stop_ma_distance.current_ma = 95.
            self.features.ma_distance.current_ma = 95.
            self.features.take_profit_distance_mean = 0.
            self.apply_entry_target(self.trade_state, EntryTarget(
                direction=-1, role='initial', quantity=Decimal(1),
                price=Decimal(100), planned_risk=Decimal(100)))

        def on_bar(self, bar):
            self.step += 1
            state = self.trade_state
            if self.step == 2:
                # Changed percentile mean must not move the locked entry; TP does move.
                self.features.bollinger = (95.0, 102.5, 110.0)
                self.features.stop_ma_distance.current_ma = 110.
                self.features.add_distance_mean = 30.
                self._refresh_protection(state)
                self._refresh_add_order(state, bar.ts_event)
                snapshots.append(state.entry_ma99)

    strategy = Scenario(make_config(close_positions_on_stop=False))
    engine = BacktestEngine(config=BacktestEngineConfig(
        logging=LoggingConfig(bypass_logging=True)))
    try:
        engine.add_venue(venue=instrument.id.venue, oms_type=OmsType.HEDGING,
                         account_type=AccountType.MARGIN, base_currency=None,
                         starting_balances=[Money(500, instrument.quote_currency)],
                         default_leverage=Decimal(5))
        engine.add_instrument(instrument)
        prices = [100, 101, 110, 95, 131] if add_first else [100, 101, 95, 131, 95]
        bars = []
        for minute, value in enumerate(prices):
            ts = 1785542400000000000 + (minute + 1) * 60_000_000_000
            px = instrument.make_price(value)
            bars.append(Bar(MINUTE_BAR_TYPE, px, px, px, px,
                            instrument.make_qty(1000), ts, ts))
        engine.add_data(bars)
        engine.add_strategy(strategy)
        engine.run()
        adds = [o for o in engine.cache.orders()
                if any(':add:' in tag for tag in o.tags or [])]
        exits = [o for o in engine.cache.orders()
                 if any(':take-profit:' in tag for tag in o.tags or [])]
        stops = [o for o in engine.cache.orders()
                 if any(':hard-stop:' in tag for tag in o.tags or [])]
        assert stops[-1].trigger_price.as_decimal() == Decimal("136.5" if add_first else "130")
        assert len(adds) == 1
        assert adds[0].price.as_decimal() == Decimal(110)
        assert adds[0].filled_qty.as_decimal() == Decimal(int(add_first))
        if not add_first:
            assert adds[0].status.name == 'CANCELED'
        assert sum(o.filled_qty.as_decimal() for o in exits) == Decimal(2 if add_first else 1)
        assert any(o.price.as_decimal() == Decimal(95) for o in exits)
        assert snapshots == [Decimal(95)]
        assert not engine.cache.positions_open()
        assert strategy.trade_state.entry_ma99 is None
    finally:
        engine.dispose()


def test_dynamic_add_updates_after_average_change_and_partial_add():
    targets = []

    class Observe(MeanReversionShort5MinStrategy):
        def apply_entry_target(self, state, target):
            targets.append(target)

    strategy = Observe(make_config())
    strategy.instrument = TestInstrumentProvider.ethusdt_perp_binance()
    state = strategy.trade_state
    state.quantity = state.unit_quantity = Decimal(1)
    state.average_price = Decimal(100)
    state.position_start_ns = 0
    strategy.features.bollinger = (90., 95., 100.)
    state.last_initial_fill_price = Decimal(100)
    state.cycle_risk_budget = Decimal(100)
    strategy.features.stop_ma_distance.current_ma = 95.
    strategy.features.stop_ma_distance.current_ma = 140.
    strategy.features.stop_p995 = 5.
    strategy.features.add_distance_mean = 10.
    strategy._lock_entry_ma99(state)
    state.quantity = Decimal('1.4')
    state.average_price = Decimal(103)
    strategy.features.bollinger = (70., 100., 130.)
    strategy._refresh_add_order(state, 1)
    assert targets[-1].price == Decimal(130)
    assert targets[-1].quantity == Decimal('.6')
    state.exit_started = True
    strategy._refresh_add_order(state, 2)
    assert len(targets) == 1


def test_partial_initial_fill_locks_once_and_waits_for_initial_completion():
    from types import SimpleNamespace

    from strategy_python.mean_reversion_short_5min.execution import OrderMeta

    targets = []
    order = SimpleNamespace(is_closed=False)

    class Observe(MeanReversionShort5MinStrategy):
        def _strategy(self):
            return SimpleNamespace(cache=SimpleNamespace(order=lambda key: order))

        def _refresh_protection(self, state):
            pass

        def apply_entry_target(self, state, target):
            targets.append(target)

    strategy = Observe(make_config())
    strategy.instrument = TestInstrumentProvider.ethusdt_perp_binance()
    state = strategy.trade_state
    state.entry_order_id = 'initial'
    strategy._order_meta['initial'] = OrderMeta(-1, 'initial', Decimal(1), planned_risk=Decimal(100))
    strategy.features.stop_ma_distance.current_ma = 140.
    strategy.features.stop_p995 = 40.
    strategy.features.add_distance_mean = 10.
    strategy.features.stop_ma_distance.current_ma = 110.
    strategy.features.bollinger = (90., 95., 100.)

    def fill(qty, price):
        strategy.on_order_filled(SimpleNamespace(
            client_order_id='initial', position_id='test', ts_event=1,
            last_qty=strategy.instrument.make_qty(qty),
            last_px=strategy.instrument.make_price(price),
            commission=Money(0, strategy.instrument.quote_currency)))

    fill(.4, 100)
    assert state.entry_ma99 == Decimal(110)
    assert state.add_distance_snapshot == Decimal(10)
    strategy.features.add_distance_mean = 20.
    strategy.features.stop_ma_distance.current_ma = 111.
    assert not targets
    strategy.features.bollinger = (80., 100., 120.)
    order.is_closed = True
    fill(.6, 102)
    assert state.average_price == Decimal('101.2')
    assert state.add_distance_snapshot == Decimal(20)
    assert state.entry_ma99 == Decimal(110)
    assert len(targets) == 1
    assert targets[0].price == Decimal(120)
    assert targets[0].quantity == Decimal(1)


def test_partial_take_profit_invalidates_add_and_late_fill_is_closed():
    from types import SimpleNamespace

    from strategy_python.mean_reversion_short_5min.execution import OrderMeta

    cleanups, cancellations = [], []

    class Observe(MeanReversionShort5MinStrategy):
        def _strategy(self):
            return SimpleNamespace(cache=SimpleNamespace(
                order=lambda key: SimpleNamespace(is_closed=False)))

        def _refresh_protection(self, state):
            pass

        def _cancel_entry(self, state):
            cancellations.append(state.entry_order_id)

        def _cancel_state_orders(self, state, exclude=None):
            pass

        def _submit_emergency_exit(self, state, reason):
            cleanups.append((state.quantity, reason))

    strategy = Observe(make_config())
    strategy.instrument = TestInstrumentProvider.ethusdt_perp_binance()
    state = strategy.trade_state
    state.quantity = state.unit_quantity = Decimal(1)
    state.average_price = Decimal(100)
    state.position_id = 'test'
    state.entry_order_id = 'add'
    strategy._order_meta['add'] = OrderMeta(-1, 'add', Decimal(1))
    strategy._order_meta['tp'] = OrderMeta(-1, 'take_profit', Decimal(1), full_exit=True)

    def fill(key, qty, price):
        strategy.on_order_filled(SimpleNamespace(
            client_order_id=key, position_id='test', ts_event=1,
            last_qty=strategy.instrument.make_qty(qty),
            last_px=strategy.instrument.make_price(price),
            commission=Money(0, strategy.instrument.quote_currency)))

    fill('tp', .4, 90)
    assert state.exit_started
    assert cancellations == ['add']
    assert strategy._order_meta['add'].invalidated
    # A fill racing with cancellation must be flattened, never start a new cycle.
    fill('add', .2, 110)
    assert cleanups == [(Decimal('.8'), 'late_entry_after_exit')]


@pytest.mark.parametrize('distance', [7., -7., 0.])
def test_initial_sizing_and_fill_snapshot_use_percentile_mean(distance):
    from collections import deque

    targets = []

    class Observe(MeanReversionShort5MinStrategy):
        def apply_entry_target(self, state, target):
            targets.append(target)

        def _account_equity(self):
            return Decimal(500)

    strategy = Observe(make_config())
    strategy.instrument = TestInstrumentProvider.ethusdt_perp_binance()
    features = strategy.features
    features.entry_touch_allowed = lambda now: True
    features.bollinger = (90., 95., 100.)  # Width 10 intentionally differs from the mean.
    features.ma_distance.current_ma = 95.
    features.ma_distance.samples = deque((i, -5.) for i in range(576))
    features.stop_ma_distance.current_ma = 95.
    features.stop_p995 = 40.
    features.add_distance_mean = distance
    strategy._refresh_initial_order(strategy.trade_state, 1)
    assert len(targets) == 1 and targets[0].price == Decimal(100)
    second = max(Decimal("100.5"), Decimal(95) + Decimal(str(distance)))
    # Stop is 30% above the projected two-unit average.
    stop = (Decimal(100) + second) / 2 * Decimal("1.3")
    cost = (stop * 2 - (Decimal(100) + second)
            + (Decimal(100) + second) * Decimal('.0002')
            + stop * 2 * Decimal('.0005')) * Decimal('1.05')
    assert targets[0].risk_per_quantity == cost
    state = strategy.trade_state
    state.last_initial_fill_price = Decimal(102)
    state.average_price = Decimal(103)
    strategy._lock_entry_ma99(state)


def test_second_entry_never_creates_third_unit():
    targets = []

    class Observe(MeanReversionShort5MinStrategy):
        def apply_entry_target(self, state, target):
            targets.append(target)

    strategy = Observe(make_config())
    state = strategy.trade_state
    state.quantity = Decimal(2)
    state.unit_quantity = Decimal(1)
    strategy._refresh_add_order(state, 1000 * 3600_000_000_000)
    assert not targets
