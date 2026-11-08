"""数据检查开关、缺失数据跳过与只读回测的行为验证。"""

import importlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from nautilus_trader.model import Bar

from engine.pipeline.catalog import ParquetDataCatalog
from nautilus_support import TestInstrumentProvider
from strategy_python import _backtesting
from strategy_python.mean_reversion_long_5min.backtest import BACKTEST_SPEC, create_strategy

START = datetime(2026, 8, 1, tzinfo=UTC)
END = START + timedelta(days=1)
ENTRIES = (
    "strategy_python.mean_reversion_long_5min.backtest",
    "strategy_python.mean_reversion_short_5min.backtest",
    "strategy_python.trend_following_long_5min.backtest",
    "strategy_python.trend_following_short_5min.backtest",
    "strategy_python.portfolio_backtest",
)


@pytest.fixture
def local_catalog(tmp_path, monkeypatch):
    catalog = ParquetDataCatalog(str(tmp_path / "catalog"))
    monkeypatch.setattr(
        _backtesting,
        "load_settings",
        lambda: SimpleNamespace(catalog_dir=Path(catalog.path)),
    )
    monkeypatch.setattr(
        _backtesting,
        "prepare_data",
        lambda *a, **kw: pytest.fail("关闭检查后不应进入 Pipeline"),
    )
    return catalog


def snapshot(path):
    return {str(p): (p.stat().st_mtime_ns, p.read_bytes()) for p in Path(path).rglob("*.parquet")}


@pytest.mark.parametrize("options", [{}, {"check_data": False}])
def test_disabled_check_runs_sparse_local_data_without_writes(local_catalog, tmp_path, options):
    instrument = TestInstrumentProvider.ethusdt_perp_binance()
    price, quantity = instrument.make_price(2000), instrument.make_qty(1)
    bars = []
    for minute in (1, 3):  # 故意缺失中间一分钟，其余日期也没有文件。
        timestamp = int((START + timedelta(minutes=minute)).timestamp() * 1_000_000_000) - 1_000_000
        bars.append(
            Bar(BACKTEST_SPEC.bar_type, price, price, price, price, quantity, timestamp, timestamp)
        )
    local_catalog.write_data([instrument])
    local_catalog.write_data(bars)
    before = snapshot(local_catalog.path)
    loaded_instrument, loaded = _backtesting.load_binance_data(
        START,
        END,
        BACKTEST_SPEC,
        **options,
    )
    assert loaded_instrument.id == instrument.id
    assert [bar.ts_event for bar in loaded] == [bar.ts_event for bar in bars]
    output = _backtesting.run_backtest(
        START,
        END,
        create_strategy(START),
        "no_check",
        BACKTEST_SPEC,
        report_dir=tmp_path / "reports",
        **options,
    )
    parameters = json.loads((output / "parameters.json").read_text())
    assert parameters["check_data"] is False
    assert parameters["backtest_spec"]["prepare_data_before_run"] is False
    assert snapshot(local_catalog.path) == before


def test_disabled_check_skips_empty_catalog_without_engine(
    local_catalog, tmp_path, monkeypatch, capsys
):
    monkeypatch.setattr(
        _backtesting, "BacktestEngine", lambda *a, **kw: pytest.fail("不应启动引擎")
    )
    output = _backtesting.run_backtest(
        START,
        END,
        create_strategy(START),
        "empty",
        BACKTEST_SPEC,
        report_dir=tmp_path / "reports",
        check_data=False,
    )
    assert output is None
    assert "跳过本次回测" in capsys.readouterr().out
    assert not (tmp_path / "reports").exists()
    assert not snapshot(local_catalog.path)


def test_enabled_check_invokes_pipeline_even_for_offline_default(local_catalog, monkeypatch):
    from strategy_python.trend_following_long_5min.backtest import BACKTEST_SPEC as offline_spec

    instrument = TestInstrumentProvider.ethusdt_perp_binance()
    local_catalog.write_data([instrument])
    calls = []

    def prepare(request, **kwargs):
        calls.append((request, kwargs))
        return SimpleNamespace(catalog_path=local_catalog.path)

    monkeypatch.setattr(_backtesting, "prepare_data", prepare)
    _backtesting.load_binance_data(START, END, offline_spec, check_data=True)
    assert len(calls) == 1
    assert calls[0][0].bar_intervals == ("1m",)
    assert calls[0][1] == {"refresh_archives": False, "force": False}


@pytest.mark.parametrize("option", ["force", "refresh_archives"])
@pytest.mark.parametrize("check_data", [None, False])
def test_disabled_check_rejects_write_flags_before_read(local_catalog, option, check_data):
    with pytest.raises(ValueError, match="--check-data"):
        _backtesting.load_binance_data(
            START, END, BACKTEST_SPEC, check_data=check_data, **{option: True}
        )


@pytest.mark.parametrize("entry", ENTRIES)
@pytest.mark.parametrize(
    "flag,expected", [("--check-data", True), (None, False)]
)
def test_cli_switch_reaches_backtest(entry, flag, expected, monkeypatch):
    module = importlib.import_module(entry)
    calls = []
    monkeypatch.setattr(module, "run_backtest", lambda *a, **kw: calls.append(kw))
    monkeypatch.setattr("sys.argv", [entry, *([flag] if flag else [])])
    module.main()
    assert calls[0]["check_data"] is expected


@pytest.mark.parametrize("entry", ENTRIES)
def test_entry_forwards_disabled_check(entry, monkeypatch):
    module = importlib.import_module(entry)
    calls = []
    target = module if entry.endswith("portfolio_backtest") else _backtesting
    method = "run_engine_backtest" if entry.endswith("portfolio_backtest") else "run_backtest"
    monkeypatch.setattr(target, method, lambda *a, **kw: calls.append(kw))
    module.run_backtest(START, END, check_data=False)
    assert calls[0]["check_data"] is False


def test_catalog_only_alias_cannot_enable_checks(monkeypatch):
    from strategy_python import portfolio_backtest

    monkeypatch.setattr(
        portfolio_backtest, "load_portfolio", lambda *a: pytest.fail("不应读取清单")
    )
    with pytest.raises(ValueError, match="--catalog-only"):
        portfolio_backtest.run_backtest(START, END, catalog_only=True, check_data=True)


@pytest.mark.parametrize("entry", ENTRIES)
def test_all_entries_default_to_local_catalog(entry, local_catalog, monkeypatch):
    module = importlib.import_module(entry)
    monkeypatch.setattr(
        _backtesting, "BacktestEngine", lambda *a, **kw: pytest.fail("空数据不应启动引擎")
    )
    assert module.run_backtest(START, END) is None


@pytest.mark.parametrize("entry", ENTRIES)
def test_removed_negative_flag_is_rejected(entry, monkeypatch):
    module = importlib.import_module(entry)
    monkeypatch.setattr(module, "run_backtest", lambda *a, **kw: pytest.fail("无效参数"))
    monkeypatch.setattr("sys.argv", [entry, "--no-check-data"])
    with pytest.raises(SystemExit) as error:
        module.main()
    assert error.value.code == 2


@pytest.mark.parametrize("option", ["force", "refresh_archives"])
def test_write_options_work_with_explicit_check(local_catalog, monkeypatch, option):
    instrument = TestInstrumentProvider.ethusdt_perp_binance()
    local_catalog.write_data([instrument])
    calls = []

    def prepare(request, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(catalog_path=local_catalog.path)

    monkeypatch.setattr(_backtesting, "prepare_data", prepare)
    _backtesting.load_binance_data(
        START, END, BACKTEST_SPEC, check_data=True, **{option: True}
    )
    assert calls[0][option] is True
