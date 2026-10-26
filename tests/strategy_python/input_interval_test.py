"""验证输入周期贯通回测入口，以及高周期聚合边界和连续性。"""

import importlib
from datetime import UTC, datetime

import pytest

from strategy_python import _backtesting
from strategy_python._indicator.bars import CompletedBar, CompletedBarAggregator

STRATEGIES = (
    "mean_reversion_long_5min",
    "mean_reversion_short_5min",
    "trend_following_long_5min",
    "trend_following_short_5min",
)


@pytest.mark.parametrize("name", STRATEGIES)
@pytest.mark.parametrize("interval,seconds", [("1s", 1), ("1m", 60), ("5m", 300)])
def test_single_backtest_passes_input_period_and_force(name, interval, seconds, monkeypatch):
    from strategy_python._indicator.bars import source_seconds_for

    module = importlib.import_module(f"strategy_python.{name}.backtest")
    captured = {}

    def run(*args, **kwargs):
        captured.update(strategy=args[2], spec=args[4], force=kwargs["force"])

    monkeypatch.setattr(_backtesting, "run_backtest", run)
    module.run_backtest(
        datetime(2026, 8, 1, tzinfo=UTC),
        datetime(2026, 8, 2, tzinfo=UTC),
        interval=interval,
        force=True,
        check_data=True,
    )
    assert captured["spec"].interval == interval
    assert captured["strategy"].config.bar_type == captured["spec"].bar_type
    assert source_seconds_for(captured["spec"].bar_type) == seconds
    assert captured["force"]


@pytest.mark.parametrize("name", STRATEGIES)
@pytest.mark.parametrize("interval", ["3m", "15m"])
def test_incompatible_period_rejected_before_engine(name, interval, monkeypatch):
    module = importlib.import_module(f"strategy_python.{name}.backtest")

    def unexpected(*args, **kwargs):
        raise AssertionError("不兼容周期不能进入回测或数据准备")

    monkeypatch.setattr(_backtesting, "run_backtest", unexpected)
    with pytest.raises(ValueError, match="整除"):
        module.run_backtest(
            datetime(2026, 8, 1, tzinfo=UTC),
            datetime(2026, 8, 2, tzinfo=UTC),
            interval=interval,
        )


@pytest.mark.parametrize("seconds", [1, 60, 300])
def test_aggregation_has_same_ohlc_and_no_early_emission(seconds):
    aggregator = CompletedBarAggregator(15, source_seconds=seconds)
    result = None
    for start in range(0, 900, seconds):
        end = start + seconds
        result = aggregator.update(
            end * 1_000_000_000 - 1_000_000,
            start,
            end + 10,
            start - 10,
            end,
        )
        if end < 900:
            assert result is None
    assert result == CompletedBar(0, 900_000_000_000, 0, 910, -10, 900)


@pytest.mark.parametrize(
    "ends", [(60, 120, 120, 240, 300), (60, 180, 120, 240, 300), (120, 180, 240, 300)]
)
def test_duplicate_out_of_order_or_partial_bucket_cannot_fill_gap(ends):
    aggregator = CompletedBarAggregator(5)
    assert all(
        aggregator.update(end * 1_000_000_000 - 1_000_000, 100, 101, 99, 100) is None
        for end in ends
    )
    for end in range(360, 601, 60):
        result = aggregator.update(end * 1_000_000_000 - 1_000_000, 100, 101, 99, 100)
    assert result is not None  # 下一完整周期能正常恢复。


@pytest.mark.parametrize("interval", ["1s", "5m"])
def test_portfolio_overrides_market_and_all_members(interval, monkeypatch):
    from strategy_python import portfolio_backtest

    captured = {}

    def run(*args, **kwargs):
        captured.update(strategies=args[2], spec=args[4], **kwargs)

    monkeypatch.setattr(portfolio_backtest, "run_engine_backtest", run)
    portfolio_backtest.run_backtest(
        datetime(2026, 8, 1, tzinfo=UTC),
        datetime(2026, 8, 2, tzinfo=UTC),
        interval=interval,
        force=True,
        check_data=True,
    )
    assert captured["force"]
    assert captured["spec"].interval == interval
    assert all(s.config.bar_type == captured["spec"].bar_type for s in captured["strategies"])
    assert captured["portfolio_parameters"]["run_config"]["interval"] == interval
