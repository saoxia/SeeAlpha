from decimal import Decimal

import pytest
from nautilus_trader.backtest import BacktestEngine
from nautilus_trader.config import BacktestEngineConfig, LoggerConfig
from nautilus_trader.model import AccountType, Bar, BarType, Money, OmsType

from nautilus_support import TestInstrumentProvider, instrument_fee_model
from strategy_python.trend_following_short_5min import (
    TrendFollowingShort5MinConfig,
    TrendFollowingShort5MinStrategy,
)
from strategy_python.trend_following_short_5min.indicators import TrendLevels


def make_strategy(cls=TrendFollowingShort5MinStrategy):
    instrument = TestInstrumentProvider.ethusdt_perp_binance()
    bar_type = BarType.from_str(f"{instrument.id}-1-MINUTE-LAST-EXTERNAL")
    strategy = cls(
        TrendFollowingShort5MinConfig(
            instrument_id=instrument.id,
            bar_type=bar_type,
            max_positions_per_side=2,
            additional_entry_spacing=0.02,
            peak_profit_exit=False,
        )
    )
    return strategy, instrument, bar_type


@pytest.mark.parametrize("direction", [-1])
@pytest.mark.parametrize(
    "price,allowed",
    [("2040", False), ("1960", False), ("2040.01", True), ("1959.99", True), ("2000", False)],
)
def test_spacing_is_strict_and_symmetric(direction, price, allowed):
    strategy, _, _ = make_strategy()
    a, b = strategy.legs
    a.record_entry_fill(Decimal(1), Decimal(2000))
    assert strategy._additional_entry_allowed(b, Decimal(price)) is allowed


def test_profit_activation_and_lines_are_independent():
    class Probe(TrendFollowingShort5MinStrategy):
        def cancel_entry(self, state):
            pass

        def submit_exit(self, state, *, half):
            pass

    strategy, _, _ = make_strategy(Probe)
    a, b = strategy.legs
    a.record_entry_fill(Decimal(1), Decimal(2000))
    b.record_entry_fill(Decimal(1), Decimal(1959))
    levels = TrendLevels(2040, 10, 2060, 2020, 2042, 2037, 2038, 2043)
    strategy._refresh_leg(a, levels, 1960)
    strategy._refresh_leg(b, levels, 1960)
    assert a.dynamic_exit_armed and not b.dynamic_exit_armed
    a.ratchet_exits(Decimal(2050), Decimal(2045))
    assert b.first_exit_level == Decimal(2038)


@pytest.mark.parametrize("direction", [-1])
def test_two_positions_keep_independent_stops_and_close_lifecycle(direction):
    class Probe(TrendFollowingShort5MinStrategy):
        count = 0

        def on_bar(self, bar):
            self.count += 1
            a, b = self.legs
            if self.count == 1:
                self.upsert_entry(a, Decimal(1), Decimal(2000))
                assert not self._additional_entry_allowed(b, Decimal(2100))
            if self.count == 3:
                price = Decimal(2000 + direction * 41)
                assert a.invested and self._additional_entry_allowed(b, price)
                self.upsert_entry(b, Decimal(1), price)
            if self.count == 4:
                assert a.invested and b.invested
                assert a.position_id != b.position_id
                assert a.stop_order_id != b.stop_order_id
                self.first_stop = a.stop_order_id
            if self.count == 5:
                assert a.invested and not b.invested
                assert a.stop_order_id == self.first_stop
                assert self.cache.order(a.stop_order_id).is_open
                assert not b.rearmed
            if self.count == 6:
                self.submit_exit(a, half=False)
            if self.count == 7:
                assert not a.invested and not b.invested
                assert not a.rearmed and not b.rearmed

    strategy, instrument, bar_type = make_strategy(Probe)
    engine = BacktestEngine(config=BacktestEngineConfig(logging=LoggerConfig(bypass_logging=True)))
    try:
        engine.add_venue(
            venue=instrument.id.venue,
            oms_type=OmsType.HEDGING,
            account_type=AccountType.MARGIN,
            base_currency=None,
            starting_balances=[Money(2000, instrument.quote_currency)],
            default_leverage=Decimal(5),
            fee_model=instrument_fee_model(instrument),
        )
        engine.add_instrument(instrument)
        bars = []
        for n in range(1, 8):
            if n == 1:
                o = h = l = c = 2000 - direction
            elif n == 2:
                o = c = 2000
                h = 2001
                l = 1999
            elif n == 3:
                o = h = l = c = 2000 + direction * 40
            else:
                o = c = 2000 + direction * 41
                h, l = c + 1, c - 1
                if n == 5:
                    if direction == 1:
                        l = 2025
                    else:
                        h = 1975
            ts = 1_780_000_000_000_000_000 + n * 60_000_000_000
            bars.append(
                Bar(
                    bar_type,
                    *[instrument.make_price(x) for x in [o, h, l, c]],
                    instrument.make_qty(100),
                    ts,
                    ts,
                )
            )
        engine.add_data(bars)
        engine.add_strategy(strategy)
        engine.run()
        positions = engine.cache.positions()
        assert len(positions) == 2 and all(p.is_closed for p in positions)
        assert not engine.cache.orders_open()
        assert all(not state.invested for state in strategy.legs)
        strategy.on_reset()
        assert len(strategy.legs) == 2
        assert all(state.rearmed and state.position_id is None for state in strategy.legs)
    finally:
        engine.dispose()
