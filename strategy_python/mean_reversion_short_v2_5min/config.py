"""五分钟均值回归空头：硬止损、均值目标和名义杠杆上限。"""

from nautilus_trader.model import BarType, InstrumentId

from strategy_python._config import PositiveFloat, PositiveInt, StrategyConfig


class MeanReversionShortV2Config(StrategyConfig, frozen=True, kw_only=True):
    instrument_id: InstrumentId
    bar_type: BarType
    order_id_tag: str = "MRS2"
    manage_contingent_orders: bool = True
    trade_start_ns: int = 0
    boll_period: PositiveInt = 20
    boll_std: PositiveFloat = 3.0
    atr_period: PositiveInt = 14
    entry_kind: str = "wick"
    regime: str = "down"
    weekend_only: bool = True
    min_mean_distance: PositiveFloat = 0.003
    stop_atr: PositiveFloat = 4.0
    min_stop_fraction: PositiveFloat = 0.004
    max_stop_fraction: PositiveFloat = 0.03
    max_holding_hours: PositiveInt = 12
    risk_fraction: PositiveFloat = 0.02
    max_notional_fraction: PositiveFloat = 3.0
    fee_buffer: PositiveFloat = 0.0007
    close_positions_on_stop: bool = True
