"""Native replay must preserve timers, fills and stop lifecycle across Arrow batches."""

from decimal import Decimal

from nautilus_trader.backtest import BacktestEngine
from nautilus_trader.config import BacktestEngineConfig, LoggerConfig
from nautilus_trader.model import AccountType, Bar, BarType, Money, OmsType, OrderSide

from engine.pipeline.catalog import ParquetDataCatalog
from nautilus_support import TestInstrumentProvider, instrument_fee_model
from strategy_python._backtesting import BarStream, data_batches
from strategy_python._strategy import Strategy


def test_native_arrow_batches_match_one_shot_lifecycle_and_fills(tmp_path):
    inst = TestInstrumentProvider.ethusdt_perp_binance()
    kind = BarType.from_str(f"{inst.id}-1-MINUTE-LAST-EXTERNAL")
    base = 1785542400000000000
    price, qty = inst.make_price(2000), inst.make_qty(10)
    rows = [
        Bar(
            kind,
            price,
            price,
            price,
            price,
            qty,
            base + i * 60_000_000_000,
            base + i * 60_000_000_000,
        )
        for i in range(1, 7)
    ]
    catalog = ParquetDataCatalog(tmp_path)
    catalog.write_data(rows)
    outcomes = []
    for streaming in (False, True):
        observed = []

        class Probe(Strategy):
            count = 0

            def __init__(self, log):
                super().__init__()
                self.observed = log

            def on_start(self):
                self.subscribe_bars(kind)
                self.clock.set_time_alert_ns(
                    "boundary", base + 150_000_000_000, lambda event: self.observed.append("timer")
                )

            def on_bar(self, bar):
                self.count += 1
                self.observed.append(bar.ts_event)
                if self.count == 1:
                    self.submit_order(
                        self.order_factory.market(inst.id, OrderSide.BUY, inst.make_qty(1))
                    )
                if self.count == 5:
                    self.close_all_positions(inst.id)

            def on_stop(self):
                self.observed.append("stop")

        engine = BacktestEngine(BacktestEngineConfig(logging=LoggerConfig(bypass_logging=True)))
        try:
            engine.add_venue(
                inst.id.venue,
                OmsType.HEDGING,
                AccountType.MARGIN,
                [Money(1000, inst.quote_currency)],
                default_leverage=Decimal(5),
                fee_model=instrument_fee_model(inst),
            )
            engine.add_instrument(inst)
            engine.add_strategy(Probe(observed))
            if streaming:
                stream = BarStream(
                    catalog.bar_batches(kind, base, base + 420_000_000_000, batch_size=2)
                )
                lengths = []
                for batch in data_batches(stream):
                    lengths.append(len(batch))
                    engine.add_data(batch)
                    engine.run(streaming=True)
                    engine.clear_data()
                engine.end()
                assert lengths == [2, 2, 2]
            else:
                engine.add_data(rows)
                engine.run()
            fills = sorted(
                (o.side.name, str(o.filled_qty), str(o.avg_px))
                for o in engine.cache.orders()
                if o.filled_qty.as_decimal() > 0
            )
            outcomes.append(
                (observed, fills, [str(p.realized_pnl) for p in engine.cache.positions()])
            )
        finally:
            engine.dispose()
    assert outcomes[0] == outcomes[1]
    assert outcomes[0][0].count("stop") == 1
    assert outcomes[0][0].index("timer") == 2
    assert len(outcomes[0][1]) == 2
