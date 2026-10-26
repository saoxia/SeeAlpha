"""回测 Binance ETHUSDT 永续合约的 v4 均值回归做多策略。"""

from datetime import datetime
from decimal import Decimal

from nautilus_trader.model import BarType, InstrumentId, OmsType

from strategy_python._backtesting import (
    BacktestSpec,
    StrategyBacktest,
    build_argument_parser,
    run_backtest_cli,
)
from strategy_python.mean_reversion_long_5min import (
    MeanReversionLong5MinConfig,
    MeanReversionLong5MinStrategy,
)

BACKTEST_WARMUP_DAYS = 135
INSTRUMENT_ID = InstrumentId.from_str("ETHUSDT-PERP.BINANCE")
BAR_TYPE = BarType.from_str("ETHUSDT-PERP.BINANCE-1-MINUTE-LAST-EXTERNAL")
BACKTEST_SPEC = BacktestSpec(
    instrument_id=INSTRUMENT_ID,
    data_symbol="ETHUSDT",
    bar_type=BAR_TYPE,
    interval="1m",
    warmup_days=BACKTEST_WARMUP_DAYS,
    oms_type=OmsType.HEDGING,
    starting_balance=Decimal(500),
    leverage=Decimal(5),
)


def create_strategy(
    start: datetime,
    *,
    bar_type: BarType = BAR_TYPE,
) -> MeanReversionLong5MinStrategy:
    """创建当前回测使用的策略实例。"""
    return MeanReversionLong5MinStrategy(
        MeanReversionLong5MinConfig(
            instrument_id=INSTRUMENT_ID,
            bar_type=bar_type,
            trade_start_ns=int(start.timestamp() * 1_000_000_000),
        ),
    )


BACKTEST = StrategyBacktest("mean_reversion_long_5min", BACKTEST_SPEC, create_strategy)
run_backtest = BACKTEST.run


def main() -> None:
    """解析命令行参数并运行回测。"""
    parser = build_argument_parser("回测 Binance ETHUSDT 永续合约均值回归做多策略")
    run_backtest_cli(run_backtest, parser)


if __name__ == "__main__":
    main()
