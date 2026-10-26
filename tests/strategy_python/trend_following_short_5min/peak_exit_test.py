from decimal import ROUND_DOWN, ROUND_UP, Decimal

import pytest
from nautilus_trader.backtest import BacktestEngine
from nautilus_trader.config import BacktestEngineConfig, LoggerConfig
from nautilus_trader.model import AccountType, Bar, BarType, Money, OmsType

from nautilus_support import TestInstrumentProvider, instrument_fee_model
from strategy_python.trend_following_short_5min import (
    TrendFollowingShort5MinConfig,
    TrendFollowingShort5MinStrategy,
)
from strategy_python.trend_following_short_5min.profit_protection import peak_profit_lock_ratio
from strategy_python.trend_following_short_5min.state import TrendLegState


@pytest.mark.parametrize("direction", [-1])
def test_peak_tracks_extreme_locks_78_percent_and_never_retreats(direction):
    s = TrendLegState(direction)
    s.record_entry_fill(Decimal(1), Decimal(100))
    s.update_profit_peak(Decimal(100) + direction * Decimal("1.99"), Decimal(".02"), Decimal(".78"))
    assert s.peak_stop_price is None
    s.update_profit_peak(Decimal(100) + direction * Decimal(2), Decimal(".02"), Decimal(".78"))
    assert s.peak_stop_price == Decimal(100) + direction * Decimal("1.56")
    s.update_profit_peak(Decimal(100) + direction * Decimal(5), Decimal(".02"), Decimal(".78"))
    assert s.peak_stop_price == Decimal(100) + direction * Decimal("3.90")
    s.update_profit_peak(Decimal(100), Decimal(".02"), Decimal(".78"))
    assert s.peak_stop_price == Decimal(100) + direction * Decimal("3.90")
    s.clear()
    assert s.peak_price is None and s.peak_stop_price is None


@pytest.mark.parametrize("direction", [-1])
@pytest.mark.parametrize("fallback", [False, True])
@pytest.mark.parametrize("dynamic", [False, True])
def test_peak_stop_only_updates_after_source_bar_and_uses_current_close_for_breach(
    direction, fallback, dynamic
):
    lock = Decimal(str(peak_profit_lock_ratio(0.02))) if dynamic else Decimal(".78")
    expected = Decimal(2000) + direction * Decimal(40) * lock
    rounded = expected.quantize(Decimal(".01"), rounding=ROUND_UP if direction > 0 else ROUND_DOWN)
    instrument = TestInstrumentProvider.ethusdt_perp_binance()
    bar_type = BarType.from_str(f"{instrument.id}-1-MINUTE-LAST-EXTERNAL")

    class Probe(TrendFollowingShort5MinStrategy):
        count = 0

        def on_bar(self, bar):
            self.count += 1
            state = self.leg
            if self.count == 1:
                self.submit_entry_market(state, Decimal(1))
            self._update_peak_exits(bar)
            if self.count == 1:
                assert state.peak_price is None
            if self.count == 2:
                if fallback:
                    assert state.exit_order_id is not None
                else:
                    assert state.invested
                    assert state.peak_stop_price == expected

    strategy = Probe(
        TrendFollowingShort5MinConfig(
            instrument_id=instrument.id,
            bar_type=bar_type,
            peak_profit_exit=True,
            peak_profit_lock=0.78,
            dynamic_peak_profit_lock=dynamic,
        )
    )
    engine = BacktestEngine(config=BacktestEngineConfig(logging=LoggerConfig(bypass_logging=True)))
    try:
        engine.add_venue(
            venue=instrument.id.venue,
            oms_type=OmsType.HEDGING,
            account_type=AccountType.MARGIN,
            base_currency=None,
            starting_balances=[Money(1000, instrument.quote_currency)],
            default_leverage=Decimal(5),
            fee_model=instrument_fee_model(instrument),
        )
        engine.add_instrument(instrument)
        bars = []
        for n in range(1, 4):
            o = 2000
            if n == 1:
                h = 2100
                l = 1900
                c = 2000
            elif n == 2:
                h, l = (2040, 2000) if direction == 1 else (2000, 1960)
                c = 2000 + direction * (20 if fallback else 38)
            else:
                o = c = 2000 + direction * 35
                h, l = (2036, 2020) if direction == 1 else (1980, 1964)
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
        p = engine.cache.positions()[0]
        assert p.is_closed
        assert p.avg_px_close == (2000 + direction * 20 if fallback else float(rounded))
        assert not engine.cache.orders_open()
    finally:
        engine.dispose()


@pytest.mark.parametrize("direction", [-1])
def test_dynamic_protection_waits_for_two_percent_and_ratchets(direction):
    state = TrendLegState(direction)
    state.record_entry_fill(Decimal(1), Decimal(100))

    def update(profit):
        state.update_profit_peak(
            Decimal(100) + direction * Decimal(profit), Decimal(".02"), Decimal(".78"), dynamic=True
        )

    update("0")
    assert state.peak_stop_price is None
    update("1")
    assert state.peak_stop_price is None
    update("1.9999")
    assert state.peak_stop_price is None
    update("2")
    assert float(state.peak_stop_price) == pytest.approx(
        100 + direction * 2 * peak_profit_lock_ratio(0.02)
    )
    armed_stop = state.peak_stop_price
    update("1")
    assert state.peak_stop_price == armed_stop
    update("3.5")
    assert float(state.peak_stop_price) == pytest.approx(100 + direction * 3.325)
    previous = state.peak_stop_price
    update("2")
    assert state.peak_stop_price == previous
    update("5")
    assert float(state.peak_stop_price) == pytest.approx(100 + direction * 4.75)
