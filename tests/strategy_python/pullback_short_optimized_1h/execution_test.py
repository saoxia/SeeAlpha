from decimal import Decimal

import pytest
from nautilus_trader.backtest import BacktestEngine
from nautilus_trader.config import BacktestEngineConfig, LoggerConfig
from nautilus_trader.model import AccountType, Bar, BarType, Money, OmsType

from nautilus_support import TestInstrumentProvider, instrument_fee_model
from strategy_python.pullback_short_optimized_1h import (
    PullbackShortOptimized1hConfig,
    PullbackShortOptimized1hStrategy,
)


@pytest.mark.parametrize("scenario", ["stop", "gap", "target", "time", "cancelled", "end"])
def test_native_protection_and_time_exit(scenario):
    instrument = TestInstrumentProvider.ethusdt_perp_binance()
    bar_type = BarType.from_str(f"{instrument.id}-1-MINUTE-LAST-EXTERNAL")

    class Probe(PullbackShortOptimized1hStrategy):
        count = 0

        def on_bar(self, bar):
            self.count += 1
            self.last_price = bar.close.as_decimal()
            if self.count == 1:
                self.enter_short(Decimal(".1"), Decimal(2020))
            else:
                super().on_bar(bar)
            if self.count == 2 and scenario == "cancelled":
                self.cancel_order(self.cache.order(self.trade.stop_id).client_order_id)

    strategy = Probe(
        PullbackShortOptimized1hConfig(
            instrument_id=instrument.id, bar_type=bar_type, reward_risk=2.0, max_holding_hours=1
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
        for n in range(1, 65):
            o = h = l = c = 2000
            if n == 3:
                if scenario == "stop":
                    h = 2025
                if scenario == "gap":
                    o = h = l = c = 2040
                if scenario == "target":
                    l = 1950
            ts = 1780000020000000000 + n * 60_000_000_000 - 1_000_000
            bars.append(
                Bar(
                    bar_type,
                    *[instrument.make_price(x) for x in (o, h, l, c)],
                    instrument.make_qty(100),
                    ts,
                    ts,
                )
            )
        if scenario == "end":
            bars = bars[:4]
        engine.add_data(bars)
        engine.add_strategy(strategy)
        engine.run()
        positions = engine.cache.positions()
        assert len(positions) == 1 and positions[0].is_closed
        # Nautilus does not deliver queued end-of-run fills to a stopped Strategy.
        # Cache positions and orders, checked here, are the authoritative final state.
        if scenario != "end":
            assert strategy.trade.quantity == 0
        assert not engine.cache.orders_open()
        fills = [o for o in engine.cache.orders() if o.filled_qty.as_double() > 0]
        assert len(fills) == 2
        exit = next(o for o in fills if o.is_reduce_only)
        if scenario == "gap":
            assert exit.avg_px == 2040
        if scenario == "stop":
            assert exit.avg_px == 2020
        if scenario == "target":
            assert exit.avg_px == 1960
        if scenario == "time":
            assert "pullback_short_optimized_1h:time_exit" in exit.tags
        if scenario == "cancelled":
            assert "pullback_short_optimized_1h:protection_failed" in exit.tags
        if scenario == "end":
            assert "pullback_short_optimized_1h:backtest_end" in exit.tags
    finally:
        engine.dispose()
