"""Independent backtest for the frozen optimized strategy."""

from datetime import UTC, datetime
from decimal import Decimal

from nautilus_trader.model import BarType, InstrumentId, OmsType

from strategy_python._backtesting import (
    BacktestSpec,
    StrategyBacktest,
    build_argument_parser,
    run_backtest_cli,
)
from strategy_python.pullback_short_optimized_1h import (
    PullbackShortOptimized1hConfig,
    PullbackShortOptimized1hStrategy,
)

INSTRUMENT_ID = InstrumentId.from_str("ETHUSDT-PERP.BINANCE")
BAR_TYPE = BarType.from_str("ETHUSDT-PERP.BINANCE-1-MINUTE-LAST-EXTERNAL")
BACKTEST_SPEC = BacktestSpec(
    instrument_id=INSTRUMENT_ID,
    data_symbol="ETHUSDT",
    bar_type=BAR_TYPE,
    interval="1m",
    warmup_days=30,
    oms_type=OmsType.HEDGING,
    starting_balance=Decimal(500),
    leverage=Decimal(5),
    prepare_data_before_run=False,
)


def create_strategy(start=None, *, bar_type=BAR_TYPE, **parameters):
    return PullbackShortOptimized1hStrategy(
        PullbackShortOptimized1hConfig(
            instrument_id=INSTRUMENT_ID,
            bar_type=bar_type,
            trade_start_ns=int(start.timestamp() * 1_000_000_000) if start else 0,
            **parameters,
        )
    )


BACKTEST = StrategyBacktest("pullback_short_optimized_1h", BACKTEST_SPEC, create_strategy)
run_backtest = BACKTEST.run


def main():
    parser = build_argument_parser("回测已冻结的独立优化策略 pullback_short_optimized_1h")
    parser.set_defaults(
        start=datetime(2021, 1, 1, tzinfo=UTC), end=datetime(2026, 9, 1, tzinfo=UTC)
    )
    run_backtest_cli(run_backtest, parser)


if __name__ == "__main__":
    main()
