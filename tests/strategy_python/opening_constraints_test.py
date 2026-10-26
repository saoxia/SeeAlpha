"""Opening bounds cannot upsize risk targets or leave stale entry orders behind."""

from decimal import Decimal

import pytest

from nautilus_support import TestInstrumentProvider
from strategy_python._execution.entry_constraints import opening_order_allowed


def instrument():
    original = TestInstrumentProvider.btcusdt_perp_binance()
    fields = original.to_dict()
    fields["min_notional"] = "50.00000000 USDT"
    return type(original).from_dict(fields)


@pytest.mark.parametrize(
    "quantity,price,allowed",
    [
        (".001", "40000", False),
        (".001", "50000", True),
        (".002", "40000", True),
        ("0", "50000", False),
        (".00001", "50000", False),
    ],
)
def test_instrument_minimum_is_checked_without_increasing_quantity(quantity, price, allowed):
    assert opening_order_allowed(instrument(), Decimal(quantity), Decimal(price)) is allowed


def test_native_margin_engine_denies_under_minimum_without_upsizing():
    from nautilus_trader.backtest import BacktestEngine
    from nautilus_trader.config import BacktestEngineConfig, LoggerConfig
    from nautilus_trader.model import AccountType, Bar, BarType, Money, OmsType, OrderSide
    from nautilus_trader.trading import Strategy

    from nautilus_support import instrument_fee_model

    inst = instrument()
    kind = BarType.from_str(f"{inst.id}-1-MINUTE-LAST-EXTERNAL")
    class Probe(Strategy):
        def on_start(self):
            self.subscribe_bars(kind)
        def on_bar(self, bar):
            if getattr(self, "sent", False):
                return
            self.sent = True
            self.submit_order(self.order_factory.limit(inst.id, OrderSide.SELL,
                inst.make_qty(Decimal(".001")), inst.make_price(40000)))
    engine = BacktestEngine(BacktestEngineConfig(logging=LoggerConfig(bypass_logging=True)))
    try:
        engine.add_venue(inst.id.venue, OmsType.HEDGING, AccountType.MARGIN,
            [Money(1000, inst.quote_currency)], default_leverage=Decimal(5),
            fee_model=instrument_fee_model(inst))
        engine.add_instrument(inst)
        engine.add_strategy(Probe())
        price, qty = inst.make_price(40000), inst.make_qty(1)
        engine.add_data([Bar(kind, price, price, price, price, qty, t, t)
                         for t in [60_000_000_000,120_000_000_000]])
        engine.run()
        order, = engine.cache.orders()
        assert order.status.name == "DENIED"
        assert order.quantity.as_decimal() == Decimal(".001")
        assert any("NOTIONAL_BELOW_MINIMUM" in str(event) for event in order.events())
        assert not engine.cache.positions_open()
    finally:
        engine.dispose()
