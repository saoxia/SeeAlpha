"""Independent fixed research configurations; never changes the current BTC registry."""

from datetime import UTC, datetime

from nautilus_trader.model import BarType, InstrumentId

from .config import RegimeMeanReversionLong5MinConfig
from .strategy import RegimeMeanReversionLong5MinStrategy

VARIANTS = ("baseline", "regime", "bounded", "combined")


def create_config(start=None, *, asset="BTC", variant="combined", bar_type=None):
    start = start or datetime(2021, 1, 1, tzinfo=UTC)
    if start.tzinfo is None or asset not in ("BTC", "ETH") or variant not in VARIANTS:
        raise ValueError("Expected an aware start, BTC/ETH, and a declared research variant")
    bounded = variant in ("bounded", "combined")
    return RegimeMeanReversionLong5MinConfig(
        instrument_id=InstrumentId.from_str(f"{asset}USDT-PERP.BINANCE"),
        bar_type=bar_type or BarType.from_str(f"{asset}USDT-PERP.BINANCE-1-MINUTE-LAST-EXTERNAL"),
        trade_start_ns=int(start.timestamp() * 1e9),
        regime_filter_enabled=variant in ("regime", "combined"),
        cycle_time_exit_hours=24.0 if bounded else 0.0,
        cycle_band_stop_multiple=2.0 if bounded else 0.0,
        cycle_rearm_after_exit=bounded,
    )


def create_strategy(start=None, *, asset="BTC", variant="combined", bar_type=None):
    return RegimeMeanReversionLong5MinStrategy(
        create_config(start, asset=asset, variant=variant, bar_type=bar_type)
    )
