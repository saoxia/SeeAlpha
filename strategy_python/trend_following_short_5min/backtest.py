"""MA20 趋势跟随策略的固定回测入口。"""

from datetime import UTC, datetime
from decimal import Decimal

from nautilus_trader.model import BarType, InstrumentId, OmsType

from strategy_python._backtesting import (
    BacktestSpec,
    StrategyBacktest,
    build_argument_parser,
    run_backtest_cli,
)
from strategy_python.trend_following_short_5min import (
    TrendFollowingShort5MinConfig,
    TrendFollowingShort5MinStrategy,
)

INSTRUMENT_ID = InstrumentId.from_str("ETHUSDT-PERP.BINANCE")
BAR_TYPE = BarType.from_str("ETHUSDT-PERP.BINANCE-1-MINUTE-LAST-EXTERNAL")
BACKTEST_START = datetime(2026, 3, 1, tzinfo=UTC)
# end 为左闭右开，9 月 1 日零点完整包含 8 月 31 日。
BACKTEST_END = datetime(2026, 9, 1, tzinfo=UTC)
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


def create_strategy(
    start: datetime | None = None,
    *,
    bar_type: BarType = BAR_TYPE,
) -> TrendFollowingShort5MinStrategy:
    trade_start_ns = int(start.timestamp() * 1_000_000_000) if start is not None else 0
    return TrendFollowingShort5MinStrategy(
        TrendFollowingShort5MinConfig(
            instrument_id=INSTRUMENT_ID,
            bar_type=bar_type,
            trade_start_ns=trade_start_ns,
        ),
    )


BACKTEST = StrategyBacktest("trend_following_short_5min", BACKTEST_SPEC, create_strategy)
run_backtest = BACKTEST.run


def main() -> None:
    parser = build_argument_parser("回测 Binance ETHUSDT MA20 做空趋势策略")
    parser.set_defaults(start=BACKTEST_START, end=BACKTEST_END)
    run_backtest_cli(run_backtest, parser)


if __name__ == "__main__":
    main()
