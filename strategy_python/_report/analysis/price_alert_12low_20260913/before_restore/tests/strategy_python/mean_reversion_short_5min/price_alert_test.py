from collections import deque
from decimal import Decimal

import pytest

from strategy_python.mean_reversion_short_5min import MeanReversionShort5MinStrategy
from tests.strategy_python.mean_reversion_short_5min.strategy_test import make_config, completed_bar


def test_rolling_low_requires_12_complete_contiguous_bars_and_resets():
    strategy = MeanReversionShort5MinStrategy(make_config())
    f = strategy.features
    step = 300_000_000_000
    for n in range(1, 13):
        f.on_five_minute_bar(completed_bar(n*step, low=80+n, minutes=5))
        assert len(f.recent_lows) == n
    assert min(f.recent_lows) == 81
    f.on_five_minute_bar(completed_bar(12*step, low=1, minutes=5))
    assert min(f.recent_lows) == 81
    f.on_five_minute_bar(completed_bar(13*step, low=95, minutes=5))
    assert min(f.recent_lows) == 82
    f.on_five_minute_bar(completed_bar(15*step, low=95, minutes=5))
    assert list(f.recent_lows) == [95]
    f.on_five_minute_bar(completed_bar(16*step, low=float('nan'), minutes=5))
    assert not f.recent_lows


def test_warning_boundary_and_locked_target_override_other_exits():
    strategy = MeanReversionShort5MinStrategy(make_config())
    state = strategy.trade_state
    state.quantity, state.average_price = Decimal(2), Decimal(100)
    strategy.features.recent_lows = deque([90.]*12, maxlen=12)
    assert not strategy._activate_price_alert(state, Decimal('109.99'))
    assert strategy._activate_price_alert(state, Decimal(110))
    assert state.exit_started and state.price_alert_limit == 90
    strategy.features.recent_lows.append(80.)
    state.breakeven_exit_active = state.no_lower_touch_exit_active = True
    assert strategy._take_profit_prices(state) == (Decimal(90), Decimal(90))
    assert not strategy._activate_price_alert(state, Decimal(120))
    state.clear_cycle()
    assert not state.price_alert_active and state.price_alert_limit is None


@pytest.mark.parametrize('scaled', [False, True])
def test_native_wick_warning_waits_then_exits_at_locked_12_bar_low(scaled):
    from nautilus_trader.backtest.engine import BacktestEngine
    from nautilus_trader.config import BacktestEngineConfig, LoggingConfig
    from nautilus_trader.model.data import Bar
    from nautilus_trader.model.enums import AccountType, OmsType
    from nautilus_trader.model.objects import Money
    from nautilus_trader.test_kit.providers import TestInstrumentProvider
    from strategy_python.mean_reversion_short_5min.position import EntryTarget
    from tests.strategy_python.mean_reversion_short_5min.strategy_test import MINUTE_BAR_TYPE

    instrument = TestInstrumentProvider.ethusdt_perp_binance()
    snapshots = []

    class Scenario(MeanReversionShort5MinStrategy):
        def on_start(self):
            super().on_start()
            self.features.bollinger = (85., 95., 105.)
            self.features.min_bollinger_width = 20.
            self.features.ma_distance.current_ma = 95.
            self.features.take_profit_distance_mean = -20.
            self.features.recent_lows = deque([90.]*12, maxlen=12)
            self.apply_entry_target(self.trade_state, EntryTarget(
                direction=-1, role='initial', quantity=Decimal(1), price=Decimal(100), planned_risk=Decimal(100)))

        def _refresh_add_order(self, state, now_ns):
            if scaled and state.phase == 'initial' and not state.exit_started:
                self.apply_entry_target(state, EntryTarget(
                    direction=-1, role='add', quantity=Decimal(1), price=Decimal(105), planned_risk=Decimal(100)))

        def on_bar(self, bar):
            super().on_bar(bar)
            snapshots.append((self.trade_state.quantity, self.trade_state.price_alert_active))
            if self.trade_state.price_alert_active:
                self.features.recent_lows.append(80.)

    engine = BacktestEngine(config=BacktestEngineConfig(logging=LoggingConfig(bypass_logging=True)))
    strategy = Scenario(make_config(close_positions_on_stop=False))
    try:
        engine.add_venue(venue=instrument.id.venue, oms_type=OmsType.HEDGING,
                         account_type=AccountType.MARGIN, base_currency=None,
                         starting_balances=[Money(1000, instrument.quote_currency)], default_leverage=Decimal(5))
        engine.add_instrument(instrument)
        bars = []
        for i, (high, low, close) in enumerate([(100,100,100),(106,105,105),(114,108,108),(120,110,110),(90,90,90)]):
            ts = 1785542400000000000 + (i+1)*60_000_000_000-1_000_000
            bars.append(Bar(MINUTE_BAR_TYPE, instrument.make_price(close), instrument.make_price(high),
                            instrument.make_price(low), instrument.make_price(close), instrument.make_qty(1000), ts, ts))
        engine.add_data(bars)
        engine.add_strategy(strategy)
        engine.run()
        quantity = Decimal(2 if scaled else 1)
        assert snapshots[2] == (quantity, True)  # Wick hit; close remains below the warning.
        assert snapshots[3] == (quantity, True)
        assert snapshots[-1][0] == 0
        exits = [o for o in engine.cache.orders() if o.is_reduce_only and o.filled_qty.as_decimal() > 0]
        assert len(exits) == 1 and exits[0].order_type.name == 'LIMIT'
        assert exits[0].price.as_decimal() == 90 and exits[0].filled_qty.as_decimal() == quantity
        assert not any(o.order_type.name == 'STOP_MARKET' for o in engine.cache.orders())
        assert strategy.trade_state.reentry_waiting
    finally:
        engine.dispose()
