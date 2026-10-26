"""Frozen research account/data configuration, independent of strategy CLI entry points."""

from decimal import Decimal

from nautilus_trader.model.data import BarType
from nautilus_trader.model.enums import OmsType
from nautilus_trader.model.identifiers import InstrumentId

from strategy_python._backtesting import BacktestSpec


def backtest_spec(key):
    return BacktestSpec(
        instrument_id=InstrumentId.from_str("ETHUSDT-PERP.BINANCE"),
        data_symbol="ETHUSDT",
        bar_type=BarType.from_str("ETHUSDT-PERP.BINANCE-1-MINUTE-LAST-EXTERNAL"),
        interval="1m",
        warmup_days={"MRL": 135, "TFL": 11, "TFS": 21, "PBS": 30}[key],
        oms_type=OmsType.HEDGING,
        starting_balance=Decimal(500),
        leverage=Decimal(5),
        prepare_data_before_run=False,
    )
