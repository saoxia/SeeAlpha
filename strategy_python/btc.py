"""Four active BTC research slots; historical variants are not additional strategies."""

from datetime import UTC, datetime
from decimal import Decimal
from importlib import import_module

from nautilus_trader.model import BarType, InstrumentId, OmsType

from strategy_python._backtesting import BacktestSpec

STRATEGIES = {
    "trend_following_long": {
        "label": "趋势跟随多头",
        "package": "btc_cycle_optimized_20260915_tfl_original",
        "prefix": "MechanismTrendFollowingLong5Min",
        "warmup_days": 11,
        "status": "historical_candidate",
    },
    "trend_following_short": {
        "label": "趋势跟随空头",
        "package": "btc_cycle_optimized_20260915_tfs_original",
        "prefix": "MechanismTrendFollowingShort5Min",
        "warmup_days": 21,
        "status": "stability_screen_failed",
    },
    "mean_reversion_long": {
        "label": "均值回归多头",
        "package": "btc_cycle_optimized_20260915_mrl_original",
        "prefix": "MechanismMeanReversionLong5Min",
        "warmup_days": 135,
        "status": "historical_candidate",
    },
    "mean_reversion_short": {
        "label": "均值回归空头",
        "package": "btc_mean_reversion_short_5min",
        "prefix": "BtcMeanReversionShort5Min",
        "warmup_days": 15,
        "status": "fixed_migration_validation_failed",
    },
}


def spec_for(key):
    info = STRATEGIES[key]
    return BacktestSpec(
        instrument_id=InstrumentId.from_str("BTCUSDT-PERP.BINANCE"),
        data_symbol="BTCUSDT",
        bar_type=BarType.from_str("BTCUSDT-PERP.BINANCE-1-MINUTE-LAST-EXTERNAL"),
        interval="1m",
        warmup_days=info["warmup_days"],
        oms_type=OmsType.HEDGING,
        starting_balance=Decimal(500),
        leverage=Decimal(5),
        prepare_data_before_run=False,
    )


def create_strategy(key, start=None, *, bar_type=None, **overrides):
    info = STRATEGIES[key]
    start = start or datetime(2021, 1, 1, tzinfo=UTC)
    if start.tzinfo is None:
        raise ValueError("Explicit timezone required")
    spec = spec_for(key)
    bar_type = bar_type or spec.bar_type
    if bar_type.instrument_id != spec.instrument_id:
        raise ValueError("BTC entry requires a BTC bar type")
    module = import_module("strategy_python." + info["package"])
    config = getattr(module, info["prefix"] + "Config")(
        instrument_id=spec.instrument_id,
        bar_type=bar_type,
        trade_start_ns=int(start.timestamp() * 1_000_000_000),
        **overrides,
    )
    return getattr(module, info["prefix"] + "Strategy")(config)
