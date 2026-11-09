"""Engine-only test Catalog writer for deterministic historical bar fixtures.

This helper intentionally requires NautilusTrader, the real Engine Catalog,
and its instrument provider. It is never imported by standalone Platform tests.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from nautilus_trader.model import Bar, BarType

from core.market.instruments import bar_type_for
from engine.pipeline.catalog import ParquetDataCatalog
from nautilus_support import TestInstrumentProvider
from tests.fixtures.strategy_packages import CLOSES


def _ohlc():
    rows = []
    previous = CLOSES[0]
    for close in CLOSES:
        high = max(previous, close) + Decimal("0.5")
        low = min(previous, close) - Decimal("0.5")
        rows.append((Decimal(previous), high, low, Decimal(close)))
        previous = close
    return rows


def write_crossover_catalog(root, start):
    instrument = TestInstrumentProvider.ethusdt_perp_binance()
    kind = BarType.from_str(bar_type_for("ETHUSDT", "1m"))
    catalog = ParquetDataCatalog(root)
    bars = []
    for index, (open_, high, low, close) in enumerate(_ohlc()):
        stamp = _ns(start + timedelta(minutes=index + 1))
        bars.append(Bar(
            kind,
            _px(instrument, open_),
            _px(instrument, high),
            _px(instrument, low),
            _px(instrument, close),
            instrument.make_qty(Decimal(10)),
            stamp,
            stamp,
        ))
    catalog.write_data(bars)
    return bars


def _px(instrument, value):
    return instrument.make_price(Decimal(format(value, ".2f")))


def _ns(value):
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    delta = value - epoch
    return (
        delta.days * 86_400 * 1_000_000_000
        + delta.seconds * 1_000_000_000
        + delta.microseconds * 1_000
    )
