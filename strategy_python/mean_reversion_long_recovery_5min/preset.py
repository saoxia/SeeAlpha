"""Fixed entry-quality research arms with the same original sizing budget."""

from datetime import UTC, datetime

from nautilus_trader.model import BarType, InstrumentId

from .config import RecoveryMeanReversionLong5MinConfig
from .strategy import RecoveryMeanReversionLong5MinStrategy

VARIANTS = ("recovery",)


def create_config(start=None, *, asset="BTC", variant="recovery"):
    start = start or datetime(2021, 1, 1, tzinfo=UTC)
    if start.tzinfo is None or asset not in ("BTC", "ETH") or variant not in (*VARIANTS, "control"):
        raise ValueError("Expected aware start, BTC/ETH, and declared variant")
    return RecoveryMeanReversionLong5MinConfig(
        instrument_id=InstrumentId.from_str(f"{asset}USDT-PERP.BINANCE"),
        bar_type=BarType.from_str(f"{asset}USDT-PERP.BINANCE-1-MINUTE-LAST-EXTERNAL"),
        trade_start_ns=int(start.timestamp() * 1e9),
        entry_cost_filter=True,
        entry_reclaim_filter=True,
        entry_recovery_breakout=variant == "recovery",
        cycle_time_exit_hours=24.0,
        cycle_band_stop_multiple=2.0,
        cycle_rearm_after_exit=True,
    )


def create_strategy(start=None, *, asset="BTC", variant="recovery"):
    return RecoveryMeanReversionLong5MinStrategy(create_config(start, asset=asset, variant=variant))
