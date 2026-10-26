"""用确定性的震荡、急跌和拉升行情检验完整交易生命周期。"""
import math
from decimal import Decimal

import pytest
from nautilus_trader.backtest import BacktestEngine
from nautilus_trader.config import BacktestEngineConfig, LoggerConfig
from nautilus_trader.model import AccountType, Bar, BarType, Money, OmsType, Quantity

from nautilus_support import TestInstrumentProvider, instrument_fee_model
from strategy_python.mean_reversion_long_5min import (
    MeanReversionLong5MinConfig,
    MeanReversionLong5MinStrategy,
)
from strategy_python.mean_reversion_long_5min.position import EntryTarget
from tests.strategy_python.mean_reversion_long_5min.strategy_test import make_config


def test_execution_preserves_direction_and_full_trade_lifecycle() -> None:
    instrument = TestInstrumentProvider.ethusdt_perp_binance()
    bar_type = BarType.from_str(f"{instrument.id}-1-MINUTE-LAST-EXTERNAL")
    start = 1785542400000000000
    bars = []
    previous = 2000
    for minute in range(5 * 1440):
        price = 2000 + 180 * math.sin(minute / 37) + 90 * math.sin(minute / 151)
        # A sharp selloff and squeeze exercise protection and recovery in both directions.
        if 3500 <= minute < 3600:
            price *= 0.55
        if 5500 <= minute < 5600:
            price *= 1.8
        timestamp = start + (minute + 1) * 60_000_000_000 - 1_000_000
        bars.append(
            Bar(
                bar_type,
                instrument.make_price(previous),
                instrument.make_price(max(previous, price) + 3),
                instrument.make_price(min(previous, price) - 3),
                instrument.make_price(price),
                Quantity.from_str("1000.000"),
                timestamp,
                timestamp,
            )
        )
        previous = price

    strategy = MeanReversionLong5MinStrategy(
        MeanReversionLong5MinConfig(
            instrument_id=instrument.id,
            bar_type=bar_type,
            risk_reference_days=1,
            risk_move_hours=4,
            ma_filter_period=4,
            ma_filter_window=4,
            recovery_ma_period=2,
            trade_start_ns=start + 2 * 1440 * 60_000_000_000,
        )
    )
    engine = BacktestEngine(
        config=BacktestEngineConfig(
            logging=LoggerConfig(bypass_logging=True),
        )
    )
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
        engine.add_data(bars)
        engine.add_strategy(strategy)
        engine.run()
        fills = engine.generate_order_fills_report()
        assert len(fills) == 17
        positions = engine.cache.positions()
        assert positions
        assert all(position.entry.name == "BUY" for position in positions)
        roles = {}
        for order in engine.cache.orders():
            if order.filled_qty.as_decimal() > 0:
                for tag in order.tags or []:
                    assert tag.startswith("ethusdt-mean-reversion-long:")
                    role = tag.split(":")[1]
                    roles[role] = roles.get(role, 0) + 1
        # Counts captured from the original strategy on this exact scenario.
        assert roles == {"initial": 3, "take-profit": 4, "add": 9, "hard-stop": 1}
        assert strategy.trade_state.hard_stop_count == 1
    finally:
        engine.dispose()


def test_execution_rejects_opposite_entry_target() -> None:
    strategy = MeanReversionLong5MinStrategy(make_config())
    with pytest.raises(ValueError, match="仅允许"):
        strategy.apply_entry_target(
            strategy.trade_state,
            EntryTarget(direction=-1, role="initial", quantity=Decimal(1), price=Decimal(2000)),
        )
