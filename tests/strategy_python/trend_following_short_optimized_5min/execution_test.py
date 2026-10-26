from decimal import Decimal

import pytest
from nautilus_trader.backtest import BacktestEngine
from nautilus_trader.config import BacktestEngineConfig, LoggerConfig
from nautilus_trader.model import AccountType, Bar, BarType, Money, OmsType

from nautilus_support import TestInstrumentProvider, instrument_fee_model
from strategy_python.trend_following_short_optimized_5min import (
    TrendFollowingShortOptimized5MinConfig,
    TrendFollowingShortOptimized5MinStrategy,
)


@pytest.mark.parametrize("direction", [-1])
@pytest.mark.parametrize("scenario", ["intrabar", "partial", "gap", "take_profit", "cancelled"])
def test_native_stop_lifecycle(direction, scenario):
    instrument = TestInstrumentProvider.ethusdt_perp_binance()
    bar_type = BarType.from_str(f"{instrument.id}-1-MINUTE-LAST-EXTERNAL")

    class Probe(TrendFollowingShortOptimized5MinStrategy):
        count = 0

        def on_bar(self, bar):
            self.count += 1
            state = self.leg
            if self.count == 1:
                self.submit_entry_market(state, Decimal(1))
            if self.count == 2 and scenario in ("partial", "take_profit"):
                self.submit_exit(state, half=scenario == "partial")
            if self.count == 2 and scenario == "cancelled":
                self.cancel_order(self.cache.order(state.stop_order_id).client_order_id)

    strategy = Probe(
        TrendFollowingShortOptimized5MinConfig(
            instrument_id=instrument.id, bar_type=bar_type, stop_loss_cooldown_seconds=10800
        )
    )
    engine = BacktestEngine(config=BacktestEngineConfig(logging=LoggerConfig(bypass_logging=True)))
    try:
        engine.add_venue(
            venue=instrument.id.venue,
            oms_type=OmsType.HEDGING,
            account_type=AccountType.MARGIN,
            base_currency=None,
            starting_balances=[Money(500, instrument.quote_currency)],
            default_leverage=Decimal(5),
            fee_model=instrument_fee_model(instrument),
        )
        engine.add_instrument(instrument)
        bars = []
        for n in range(1, 5):
            o = h = l = c = 2000
            if n == 3:
                if scenario == "gap":
                    o = h = l = c = 2000 - direction * 20
                elif direction == 1:
                    l = 1980
                else:
                    h = 2020
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
        assert len(positions) == 1 and positions[0].is_closed
        orders = engine.cache.orders()
        stops = [o for o in orders if o.order_type.name == "STOP_MARKET"]
        assert len(stops) == 1
        stop = stops[0]
        assert stop.trigger_price.as_decimal() == Decimal(1990 if direction == 1 else 2010)
        assert stop.is_reduce_only
        if scenario in ("take_profit", "cancelled"):
            assert stop.is_canceled
        else:
            assert stop.status.name == "FILLED"
            assert stop.filled_qty.as_decimal() == Decimal(".5" if scenario == "partial" else "1")
            # This fill happens during the third minute, without a 5-minute close signal.
            assert stop.ts_last == bars[2].ts_event
            if scenario == "gap":
                assert stop.avg_px == 2000 - direction * 20
        state = strategy.leg
        if scenario in ("take_profit", "cancelled"):
            assert state.stop_cooldown_until_ns == 0
        else:
            until = stop.ts_last + 10800 * 1_000_000_000
            assert state.stop_cooldown_until_ns == until
            assert state.stop_cooldown_active(until - 1)
            assert not state.stop_cooldown_active(until)
        assert not engine.cache.orders_open()
        assert not strategy.leg.invested
    finally:
        engine.dispose()
