from decimal import Decimal

import pytest

from nautilus_support import TestInstrumentProvider, make_strategy_price


@pytest.mark.parametrize("value,expected", [
    ("103.525", "103.53"), ("103.515", "103.52"),
    ("100.005", "100.00"), ("100.025", "100.03"),
])
def test_order_price_preserves_reference_levels(value, expected):
    # Reference order levels were recorded during the engine migration.
    instrument = TestInstrumentProvider.ethusdt_perp_binance()
    assert make_strategy_price(instrument, Decimal(value)).as_decimal() == Decimal(expected)


def test_config_preserves_native_runtime_fields_and_json_roundtrip():
    from nautilus_trader.model import BarType, OmsType, StrategyId
    from nautilus_trader.trading import StrategyConfig as NativeConfig

    from strategy_python.trend_following_long_5min import TrendFollowingLong5MinConfig

    instrument = TestInstrumentProvider.ethusdt_perp_binance()
    config = TrendFollowingLong5MinConfig(
        instrument_id=instrument.id,
        bar_type=BarType.from_str(f"{instrument.id}-1-MINUTE-LAST-EXTERNAL"),
        strategy_id=StrategyId("COMPAT-001"), oms_type=OmsType.HEDGING,
        manage_gtd_expiry=True, log_commands=False,
    )
    assert isinstance(config, NativeConfig)
    assert config.strategy_id == StrategyId("COMPAT-001")
    assert config.manage_gtd_expiry and not config.log_commands
    assert config.oms_type == OmsType.HEDGING
    assert TrendFollowingLong5MinConfig.parse(config.json()) == config
    with pytest.raises(AttributeError, match="immutable"):
        config.ma_period = 3
    with pytest.raises(TypeError, match="Unknown"):
        TrendFollowingLong5MinConfig(typo=1)
