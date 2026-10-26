"""Standalone ETH/BTC mechanism candidate backtests."""

from datetime import UTC, datetime
from decimal import Decimal

from nautilus_trader.model import BarType, InstrumentId, OmsType

from strategy_python._backtesting import (
    BacktestSpec,
    StrategyBacktest,
    build_argument_parser,
    run_backtest_cli,
)
from strategy_python._mechanism.factory import STRATEGIES, create_strategy


def run(asset, key, variant="main", **arguments):
    spec = BacktestSpec(
        instrument_id=InstrumentId.from_str(f"{asset}USDT-PERP.BINANCE"),
        data_symbol=f"{asset}USDT",
        bar_type=BarType.from_str(f"{asset}USDT-PERP.BINANCE-1-MINUTE-LAST-EXTERNAL"),
        interval="1m",
        warmup_days=STRATEGIES[key]["warmup_days"],
        oms_type=OmsType.HEDGING,
        starting_balance=Decimal(500),
        leverage=Decimal(5),
        prepare_data_before_run=False,
    )

    def factory(start, *, bar_type=spec.bar_type):
        if bar_type != spec.bar_type:
            raise ValueError("Mechanism candidate currently requires one-minute input")
        return create_strategy(asset, key, start, variant=variant)

    return StrategyBacktest(f"mechanism_{asset}_{key}_{variant}", spec, factory).run(**arguments)


def main():
    parser = build_argument_parser("回测按条件路径统计定义的ETH/BTC机制候选")
    parser.add_argument("asset", choices=["ETH", "BTC"])
    parser.add_argument("key", choices=STRATEGIES)
    parser.add_argument("--variant", default="main")
    parser.set_defaults(
        start=datetime(2024, 1, 1, tzinfo=UTC), end=datetime(2026, 8, 31, tzinfo=UTC)
    )
    run_backtest_cli(run, parser)


if __name__ == "__main__":
    main()
