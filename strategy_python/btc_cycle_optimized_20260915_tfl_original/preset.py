"""Frozen research preset. No order submission occurs on import."""
from datetime import UTC, datetime

from nautilus_trader.model import BarType, InstrumentId

from .config import MechanismTrendFollowingLong5MinConfig
from .strategy import MechanismTrendFollowingLong5MinStrategy


def create_config(start=None):
    start = start or datetime(2021, 1, 1, tzinfo=UTC)
    if start.tzinfo is None:
        raise ValueError("Explicit timezone required")
    return MechanismTrendFollowingLong5MinConfig(instrument_id=InstrumentId.from_str("BTCUSDT-PERP.BINANCE"), bar_type=BarType.from_str("BTCUSDT-PERP.BINANCE-1-MINUTE-LAST-EXTERNAL"), trade_start_ns=int(start.timestamp()*1e9))

def create_strategy(start=None):
    return MechanismTrendFollowingLong5MinStrategy(create_config(start))
