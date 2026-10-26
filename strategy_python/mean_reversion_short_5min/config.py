"""当前均值回归做空策略参数。"""
from nautilus_trader.model import BarType, InstrumentId

from strategy_python._config import PositiveInt, StrategyConfig


class MeanReversionShort5MinConfig(StrategyConfig, frozen=True):
    instrument_id: InstrumentId
    bar_type: BarType
    trade_start_ns: int = 0
    max_units: PositiveInt = 2
    bollinger_period: PositiveInt = 20
    bollinger_std_multiplier: float = 2.0
    entry_ma_distance_quantile: float = 0.11
    cycle_risk_fraction: float = 0.30
    entry_fee_rate: float = 0.0002
    forced_exit_fee_rate: float = 0.0005
    stop_cost_buffer: float = 0.05
    time_exit_loss_step: float = 0.01
    time_exit_max_loss: float = 0.05
    close_positions_on_stop: bool = True
