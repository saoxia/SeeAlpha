"""BTC-USDT永续的独立回测入口；保留来源策略默认交易参数。"""

from datetime import UTC, datetime
from decimal import Decimal

from nautilus_trader.model import BarType, InstrumentId, OmsType

from strategy_python._backtesting import (
    BacktestSpec,
    StrategyBacktest,
    build_argument_parser,
    run_backtest_cli,
)
from strategy_python.btc_pullback_short_optimized_1h import (
    BtcPullbackShortOptimized1hConfig,
    BtcPullbackShortOptimized1hStrategy,
)

INSTRUMENT_ID = InstrumentId.from_str("BTCUSDT-PERP.BINANCE")
BAR_TYPE = BarType.from_str("BTCUSDT-PERP.BINANCE-1-MINUTE-LAST-EXTERNAL")
BACKTEST_SPEC = BacktestSpec(
    instrument_id=INSTRUMENT_ID,
    data_symbol="BTCUSDT",
    bar_type=BAR_TYPE,
    interval="1m",
    warmup_days=30,
    oms_type=OmsType.HEDGING,
    starting_balance=Decimal(500),
    leverage=Decimal(5),
    prepare_data_before_run=False,
)


def create_strategy(start=None, *, bar_type=BAR_TYPE, **parameters):
    return BtcPullbackShortOptimized1hStrategy(
        BtcPullbackShortOptimized1hConfig(
            instrument_id=INSTRUMENT_ID,
            bar_type=bar_type,
            trade_start_ns=int(start.timestamp() * 1_000_000_000) if start else 0,
            **parameters,
        )
    )


BACKTEST = StrategyBacktest("btc_pullback_short_optimized_1h", BACKTEST_SPEC, create_strategy)
run_backtest = BACKTEST.run


def main():
    parser = build_argument_parser("回测BTC-USDT策略 btc_pullback_short_optimized_1h")
    parser.set_defaults(
        start=datetime(2021, 1, 1, tzinfo=UTC), end=datetime(2026, 8, 31, tzinfo=UTC)
    )
    run_backtest_cli(run_backtest, parser)


if __name__ == "__main__":
    main()
