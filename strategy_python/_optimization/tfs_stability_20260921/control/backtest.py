"""Independent backtest for the frozen optimized strategy."""

from datetime import UTC, datetime
from decimal import Decimal

from nautilus_trader.model.data import BarType
from nautilus_trader.model.enums import OmsType
from nautilus_trader.model.identifiers import InstrumentId

from strategy_python._backtesting import (
    BacktestSpec,
    StrategyBacktest,
    build_argument_parser,
    run_backtest_cli,
)
from strategy_python._optimization.tfs_stability_20260921.control import (
    TrendFollowingShortOptimized5MinConfig,
    TrendFollowingShortOptimized5MinStrategy,
)

INSTRUMENT_ID = InstrumentId.from_str("ETHUSDT-PERP.BINANCE")
BAR_TYPE = BarType.from_str("ETHUSDT-PERP.BINANCE-1-MINUTE-LAST-EXTERNAL")
BACKTEST_SPEC = BacktestSpec(
    instrument_id=INSTRUMENT_ID,
    data_symbol="ETHUSDT",
    bar_type=BAR_TYPE,
    interval="1m",
    warmup_days=21,
    oms_type=OmsType.HEDGING,
    starting_balance=Decimal(500),
    leverage=Decimal(5),
    prepare_data_before_run=False,
)


def create_strategy(start=None, *, bar_type=BAR_TYPE, **parameters):
    return TrendFollowingShortOptimized5MinStrategy(
        TrendFollowingShortOptimized5MinConfig(
            instrument_id=INSTRUMENT_ID,
            bar_type=bar_type,
            trade_start_ns=int(start.timestamp() * 1_000_000_000) if start else 0,
            **parameters,
        )
    )


BACKTEST = StrategyBacktest("trend_following_short_optimized_5min", BACKTEST_SPEC, create_strategy)
run_backtest = BACKTEST.run


def main():
    parser = build_argument_parser("回测已冻结的独立优化策略 trend_following_short_optimized_5min")
    parser.set_defaults(
        start=datetime(2021, 1, 1, tzinfo=UTC), end=datetime(2026, 9, 1, tzinfo=UTC)
    )
    run_backtest_cli(run_backtest, parser)


if __name__ == "__main__":
    main()
