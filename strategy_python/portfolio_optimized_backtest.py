"""Explicit entry point for frozen optimized members, preserving original registrations."""

from datetime import UTC, datetime
from importlib import import_module
from pathlib import Path

from strategy_python._backtesting import build_argument_parser, run_backtest_cli
from strategy_python.portfolio import STRATEGY_REGISTRY
from strategy_python.portfolio_backtest import run_backtest as run_portfolio_backtest

DEFAULT_CONFIG = Path(__file__).with_name("_portfolio") / "four_strategies_optimized_20260914.json"
OPTIMIZED_STRATEGIES = {
    "mean_reversion_long_optimized_5min": "MeanReversionLongOptimized5Min",
    "trend_following_long_optimized_5min": "TrendFollowingLongOptimized5Min",
    "trend_following_short_optimized_5min": "TrendFollowingShortOptimized5Min",
    "pullback_short_optimized_1h": "PullbackShortOptimized1h",
}


def register_optimized_strategies():
    """Add distinct registry keys only when the optimized entry point is requested."""
    for name, prefix in OPTIMIZED_STRATEGIES.items():
        module = import_module(f"strategy_python.{name}")
        pair = (getattr(module, prefix + "Config"), getattr(module, prefix + "Strategy"))
        existing = STRATEGY_REGISTRY.get(name)
        if existing is not None and existing != pair:
            raise ValueError(f"Strategy registration conflict: {name}")
        STRATEGY_REGISTRY[name] = pair


def run_backtest(start, end, *, config_path=DEFAULT_CONFIG, **options):
    register_optimized_strategies()
    return run_portfolio_backtest(start, end, config_path=config_path, **options)


def main():
    parser = build_argument_parser("回测四个独立优化策略的共享账户组合")
    parser.add_argument("--config", dest="config_path", type=Path, default=DEFAULT_CONFIG)
    parser.set_defaults(
        start=datetime(2021, 1, 1, tzinfo=UTC), end=datetime(2026, 9, 1, tzinfo=UTC)
    )
    run_backtest_cli(run_backtest, parser)


if __name__ == "__main__":
    main()
