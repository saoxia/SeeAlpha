"""Frozen research preset. No order submission occurs on import."""
from datetime import UTC, datetime
from nautilus_trader.model.data import BarType
from nautilus_trader.model.identifiers import InstrumentId
from .config import MechanismMeanReversionLong5MinConfig
from .strategy import MechanismMeanReversionLong5MinStrategy

def create_config(start=None):
    start = start or datetime(2021, 1, 1, tzinfo=UTC)
    if start.tzinfo is None:
        raise ValueError("Explicit timezone required")
    return MechanismMeanReversionLong5MinConfig(instrument_id=InstrumentId.from_str("BTCUSDT-PERP.BINANCE"), bar_type=BarType.from_str("BTCUSDT-PERP.BINANCE-1-MINUTE-LAST-EXTERNAL"), trade_start_ns=int(start.timestamp()*1e9))

def create_strategy(start=None):
    return MechanismMeanReversionLong5MinStrategy(create_config(start))
