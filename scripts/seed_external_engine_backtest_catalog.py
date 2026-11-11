"""Seed synthetic 1-minute bars *inside a disposable Engine Docker container*.

Executed only via docker exec -i <disposable-cid> python - < this_file.
Requires Engine image's NautilusTrader; never imports it on the Platform host.
No network calls, exchanges, account credentials or live market data.
"""

import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path


def main():
    if os.environ.get("SEEALPHA_SMOKE_DISPOSABLE") != "1":
        raise SystemExit("Refusing to create Catalog outside a marked disposable container")
    if Path.cwd().resolve() != Path("/app"):
        raise SystemExit("Unexpected working directory: Engine smoke requires /app")

    data_root = Path("/app/data")
    if not data_root.is_dir() or data_root.is_symlink() or not data_root.is_mount():
        raise SystemExit("Disposable Engine data mount is missing or invalid")
    catalog_root = data_root / "catalog"
    if catalog_root.exists() and any(catalog_root.iterdir()):
        raise SystemExit("Refusing to overwrite an existing Catalog")
    catalog_root.mkdir(mode=0o700, exist_ok=True)

    from nautilus_trader.model import Bar, BarType

    from core.market.instruments import bar_type_for
    from engine.pipeline.catalog import ParquetDataCatalog
    from nautilus_support import TestInstrumentProvider

    CLOSES = [100] * 8 + [101, 102, 104, 108, 116, 130, 120, 100, 80, 60, 40]
    instrument = TestInstrumentProvider.ethusdt_perp_binance()

    def price(value):
        return instrument.make_price(Decimal(format(value, ".2f")))
    bar_type = BarType.from_str(bar_type_for("ETHUSDT", "1m"))
    start = datetime(2024, 1, 1, tzinfo=UTC)
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    previous = CLOSES[0]
    bars = []
    for index, close in enumerate(CLOSES):
        high = max(previous, close) + Decimal("0.5")
        low = min(previous, close) - Decimal("0.5")
        stamp = start + timedelta(minutes=index + 1)
        diff = stamp - epoch
        timestamp = (
            diff.days * 86_400 * 1_000_000_000
            + diff.seconds * 1_000_000_000
            + diff.microseconds * 1_000
        )

        bars.append(Bar(
            bar_type,
            price(previous),
            price(high),
            price(low),
            price(close),
            instrument.make_qty(Decimal(10)),
            timestamp,
            timestamp,
        ))
        previous = close

    ParquetDataCatalog(catalog_root).write_data(bars)
    print(f"PASS: synthetic ETHUSDT 1m Catalog, {len(bars)} bars in disposable Engine volume")


if __name__ == "__main__":
    main()
