"""ETHUSDT 五分钟均值回归做空，3～5倍名义杠杆上限。"""

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

from nautilus_trader.model import BarType, InstrumentId, OmsType

from strategy_python._backtesting import (
    BacktestSpec,
    StrategyBacktest,
    build_argument_parser,
    run_backtest_cli,
)
from strategy_python.mean_reversion_short_v2_5min import (
    MeanReversionShortV2Config,
    MeanReversionShortV2Strategy,
)

INSTRUMENT_ID = InstrumentId.from_str("ETHUSDT-PERP.BINANCE")
BAR_TYPE = BarType.from_str("ETHUSDT-PERP.BINANCE-1-MINUTE-LAST-EXTERNAL")
BACKTEST_SPEC = BacktestSpec(
    instrument_id=INSTRUMENT_ID,
    data_symbol="ETHUSDT",
    bar_type=BAR_TYPE,
    interval="1m",
    warmup_days=3,
    oms_type=OmsType.HEDGING,
    starting_balance=Decimal(500),
    leverage=Decimal(3),
    prepare_data_before_run=False,
)


def create_strategy(start=None, *, bar_type=BAR_TYPE, **parameters):
    return MeanReversionShortV2Strategy(
        MeanReversionShortV2Config(
            instrument_id=INSTRUMENT_ID,
            bar_type=bar_type,
            trade_start_ns=int(start.timestamp() * 1e9) if start else 0,
            **parameters,
        )
    )


def run_backtest(start, end, *, max_leverage=3.0, **parameters):
    if not 3 <= max_leverage <= 5:
        raise ValueError("max_leverage 必须在3到5之间")
    return StrategyBacktest(
        "mean_reversion_short_v2_5min",
        replace(BACKTEST_SPEC, leverage=Decimal(str(max_leverage))),
        create_strategy,
    ).run(start, end, max_notional_fraction=float(max_leverage), **parameters)


def main():
    parser = build_argument_parser("五分钟均值回归做空 v2")
    parser.set_defaults(
        start=datetime(2021, 1, 1, tzinfo=UTC), end=datetime(2026, 9, 1, tzinfo=UTC)
    )
    parser.add_argument(
        "--max-leverage", type=float, default=3, help="账户及开仓名义杠杆上限，3到5之间"
    )
    run_backtest_cli(run_backtest, parser)


if __name__ == "__main__":
    main()
