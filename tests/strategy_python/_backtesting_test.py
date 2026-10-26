from datetime import UTC, datetime

import pytest
from nautilus_trader.model import BarType, InstrumentId

from strategy_python._backtesting import BacktestSpec, parse_utc_datetime, run_backtest


def test_parse_utc_datetime() -> None:
    assert parse_utc_datetime("2026-08-01") == datetime(2026, 8, 1, tzinfo=UTC)


def test_time_range_must_be_in_order(tmp_path) -> None:
    moment = datetime(2026, 8, 1, tzinfo=UTC)
    spec = BacktestSpec(
        instrument_id=InstrumentId.from_str("ETHUSDT-PERP.BINANCE"),
        data_symbol="ETHUSDT",
        bar_type=BarType.from_str("ETHUSDT-PERP.BINANCE-15-MINUTE-LAST-EXTERNAL"),
        interval="15m",
    )

    with pytest.raises(ValueError, match="开始时间必须早于结束时间"):
        run_backtest(moment, moment, None, "TEST", spec, report_dir=tmp_path)  # type: ignore[arg-type]


def test_reports_preserve_runs_and_write_standard_json(tmp_path) -> None:
    import json

    import pandas as pd

    from strategy_python._backtesting import save_backtest_reports

    reports = {"fills": pd.DataFrame({"quantity": [2]}, index=["order-1"])}
    first = save_backtest_reports(
        tmp_path,
        reports,
        parameters={"period": 20},
        summary={"sharpe": float("nan")},
    )
    original = (first / "fills.csv").read_bytes()
    second = save_backtest_reports(tmp_path, reports, parameters={"period": 52}, summary={})
    assert first != second
    assert (first / "fills.csv").read_bytes() == original
    assert pd.read_csv(first / "fills.csv", index_col=0).loc["order-1", "quantity"] == 2
    assert json.loads((first / "parameters.json").read_text()) == {"period": 20}
    assert json.loads((second / "parameters.json").read_text()) == {"period": 52}
    assert json.loads((first / "summary.json").read_text()) == {"sharpe": None}


def test_real_engine_exports_empty_trade_reports(tmp_path, monkeypatch) -> None:
    import json
    from datetime import timedelta

    from nautilus_trader.model import Bar, Price, Quantity

    from nautilus_support import TestInstrumentProvider
    from strategy_python import _backtesting
    from strategy_python.trend_following_long_5min.backtest import (
        BACKTEST_SPEC,
        BAR_TYPE,
        create_strategy,
    )

    start = datetime(2026, 8, 1, tzinfo=UTC)
    timestamp = int(start.timestamp() * 1_000_000_000)
    instrument = TestInstrumentProvider.ethusdt_perp_binance()
    bar = Bar(
        BAR_TYPE,
        Price.from_str("2000.00"),
        Price.from_str("2001.00"),
        Price.from_str("1999.00"),
        Price.from_str("2000.00"),
        Quantity.from_str("1.000"),
        timestamp,
        timestamp,
    )
    monkeypatch.setattr(_backtesting, "load_binance_data", lambda *a, **kw: (instrument, [bar]))
    run_backtest(
        start,
        start + timedelta(days=1),
        create_strategy(start),
        "trend_following_long_5min",
        BACKTEST_SPEC,
        report_dir=tmp_path,
    )
    (output,) = tmp_path.iterdir()
    assert {p.name for p in output.iterdir()} == {
        "account.csv",
        "fills.csv",
        "positions.csv",
        "parameters.json",
        "summary.json",
        "report.html",
    }
    parameters = json.loads((output / "parameters.json").read_text())
    assert parameters["strategy_config"]["ma_period"] == 20
    assert parameters["start"] == start.isoformat()
    assert parameters["backtest_spec"]["starting_balance"] == "500"
    summary = json.loads((output / "summary.json").read_text())
    assert summary["total_orders"] == 0


def test_registered_strategy_reuses_common_runner_and_returns_result(tmp_path, monkeypatch):
    from strategy_python import _backtesting
    from strategy_python.trend_following_long_5min.backtest import BACKTEST_SPEC, create_strategy

    calls = []

    def engine(*args, **kwargs):
        calls.append((args, kwargs))
        return tmp_path if len(calls) == 1 else None

    monkeypatch.setattr(_backtesting, "run_backtest", engine)
    entry = _backtesting.StrategyBacktest("new_strategy", BACKTEST_SPEC, create_strategy)
    start = datetime(2026, 8, 1, tzinfo=UTC)
    end = datetime(2026, 8, 2, tzinfo=UTC)
    assert entry.run(start, end, interval="5m", check_data=False) == tmp_path
    assert entry.run(start, end, check_data=False) is None
    first, kwargs = calls[0]
    second, _ = calls[1]
    assert first[2] is not second[2]  # 每次回测独立创建策略，不能复用上次状态。
    assert first[2].leg.direction == 1
    assert second[2].leg.direction == 1
    assert first[4].interval == "5m"
    assert first[2].config.bar_type == first[4].bar_type
    assert kwargs["report_dir"].parts[-2:] == ("backtest", "new_strategy")
    assert kwargs["check_data"] is False
    assert entry.spec.interval == "1m"  # 覆盖运行周期不修改注册的默认值。


@pytest.mark.parametrize("invalid", ["dates", "flags"])
def test_registered_entry_rejects_invalid_options_before_factory(invalid):
    from strategy_python._backtesting import StrategyBacktest
    from strategy_python.trend_following_long_5min.backtest import BACKTEST_SPEC

    def unexpected(*args, **kwargs):
        pytest.fail("无效回测参数不应创建策略")

    entry = StrategyBacktest("new_strategy", BACKTEST_SPEC, unexpected)
    start = datetime(2026, 8, 1, tzinfo=UTC)
    end = start if invalid == "dates" else datetime(2026, 8, 2, tzinfo=UTC)
    options = {"force": True, "check_data": False} if invalid == "flags" else {}
    with pytest.raises(ValueError):
        entry.run(start, end, **options)


def test_common_cli_preserves_strategy_options_and_default_dates(monkeypatch):
    from strategy_python.trend_following_long_5min import backtest

    calls = []
    monkeypatch.setattr(backtest, "run_backtest", lambda **kwargs: calls.append(kwargs))
    monkeypatch.setattr(
        "sys.argv", ["backtest"]
    )
    backtest.main()
    assert "mode" not in calls[0]
    assert calls[0]["start"] == backtest.BACKTEST_START
    assert calls[0]["end"] == backtest.BACKTEST_END
    assert calls[0]["check_data"] is False


def test_common_cli_maps_portfolio_config_and_reports_invalid_flags(monkeypatch, capsys):
    from strategy_python import portfolio_backtest

    calls = []
    monkeypatch.setattr(portfolio_backtest, "run_backtest", lambda **kwargs: calls.append(kwargs))
    monkeypatch.setattr("sys.argv", ["portfolio", "--config", "custom.json", "--catalog-only"])
    portfolio_backtest.main()
    assert str(calls[0]["config_path"]) == "custom.json"
    assert calls[0]["catalog_only"] is True

    from strategy_python.mean_reversion_long_5min import backtest

    monkeypatch.setattr("sys.argv", ["long", "--force"])
    with pytest.raises(SystemExit) as error:
        backtest.main()
    assert error.value.code == 2
    assert "--check-data" in capsys.readouterr().err
