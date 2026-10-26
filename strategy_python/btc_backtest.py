"""One explicit backtest entry for each of the four BTC strategy families/directions."""

from functools import partial

from strategy_python._backtesting import (
    StrategyBacktest,
    build_argument_parser,
    run_backtest_cli,
)
from strategy_python.btc import STRATEGIES, create_strategy, spec_for


def main():
    parser = build_argument_parser("回测BTC现行研究策略：趋势/均值回归，多空各一个")
    parser.add_argument("strategy", choices=STRATEGIES)

    def run(strategy, **kwargs):
        factory = partial(create_strategy, strategy)
        backtest = StrategyBacktest("btc_current_" + strategy, spec_for(strategy), factory)
        return backtest.run(**kwargs)

    run_backtest_cli(run, parser)


if __name__ == "__main__":
    main()
