"""Hierarchical checks read real rows and only add confirmed missing evidence."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from nautilus_trader.model import Bar, BarType, Price, Quantity

from core.market.instruments import BINANCE_KLINE_INTERVALS, bar_type_for, bar_window
from nautilus_support import TestInstrumentProvider
from pipeline.catalog import ParquetDataCatalog
from pipeline.models import PipelineSettings
from pipeline.pair_data import calculate_history_change
from pipeline.pair_repair import install_tick_slice, reconcile_day, remote_klines

DAY = datetime(2026, 8, 1, tzinfo=UTC)
LEFT = int(DAY.timestamp() * 1000)


def minutes(catalog, *, missing=(), wrong=None):
    catalog.write_data([TestInstrumentProvider.ethusdt_perp_binance()])
    result = []
    for minute in range(1440):
        if minute in missing:
            continue
        stamp = (LEFT + (minute + 1) * 60000 - 1) * 1000000
        result.append(Bar(BarType.from_str(bar_type_for("ETHUSDT", "1m")),
                          *(Price.from_str(value) for value in ("10.00", "12.00", "9.00", "11.00")),
                          Quantity.from_str("2.000" if minute == wrong else "1.000"), stamp, stamp))
    catalog.write_data(result)


class Rest:
    def __init__(self):
        self.requests = []

    async def get(self, endpoint, **params):
        assert endpoint == "klines"
        self.requests.append(params)
        level, cursor, end = params["interval"], params["startTime"], params["endTime"] + 1
        rows = []
        while cursor < end and len(rows) < params["limit"]:
            left, right = bar_window(level, cursor)
            count = (right - left) // 60000
            rows.append([left, "10.00", "12.00", "9.00", "11.00", str(count), right - 1, "0", count, "0", "0", "0"])
            cursor = right
        return rows


@pytest.fixture
def dataset(tmp_path):
    settings = PipelineSettings("futures_um", "trades", ("ETHUSDT",), ("1m",),
                                tmp_path / "raw", tmp_path / "catalog", tmp_path / "state")
    return settings, ParquetDataCatalog(settings.catalog_dir)


def check(settings, rest):
    return asyncio.run(reconcile_day({"symbol": "ETHUSDT"}, settings, rest, "1m", DAY, DAY + timedelta(days=1), LEFT))


def test_equal_day_never_requests_smaller_periods(dataset):
    settings, catalog = dataset
    minutes(catalog)
    rest = Rest()
    result = check(settings, rest)
    assert result["ready"] and result["records_written"] == 0
    assert [request["interval"] for request in rest.requests] == ["1d"]


def test_missing_minute_inside_existing_file_is_located_and_added_once(dataset):
    settings, catalog = dataset
    minutes(catalog, missing=(500,))
    rest = Rest()
    result = check(settings, rest)
    assert result["ready"] and result["records_written"] == 1 and result["candidates"] == 1
    levels = [request["interval"] for request in rest.requests]
    assert levels[:6] == ["1d", "4h", "1h", "15m", "5m", "1m"]
    assert sum(level == "1m" for level in levels) == 1
    assert len(catalog.bars()) == 1440
    repeated = Rest()
    assert check(settings, repeated)["records_written"] == 0
    assert [request["interval"] for request in repeated.requests] == ["1d"]


def test_empty_history_is_filled_and_rest_pages_are_not_truncated(dataset):
    settings, catalog = dataset
    minutes(catalog, missing=range(1440))
    result = check(settings, Rest())
    assert result["ready"] and result["records_written"] == 1440
    assert len(catalog.bars()) == 1440
    rest = Rest()
    rows = asyncio.run(remote_klines(rest, "ETHUSDT", "1m", LEFT, LEFT + 86400000, LEFT))
    assert len(rows) == 1440 and len(rest.requests) == 2


def test_value_conflicts_are_unresolved_and_preserve_original_rows(dataset):
    settings, catalog = dataset
    minutes(catalog, wrong=500)
    before = catalog.bars()
    result = check(settings, Rest())
    assert not result["ready"] and result["records_written"] == 0
    assert result["remaining_minutes"] == [LEFT + 500 * 60000]
    assert result["conflicts"][0]["reason"] == "已有分钟来源值冲突"
    assert catalog.bars() == before


@pytest.mark.parametrize("failure", ["empty", "gap", "nan", "forming", "duplicate"])
def test_invalid_remote_pages_never_become_verified(failure):
    class Invalid(Rest):
        async def get(self, endpoint, **params):
            rows = await super().get(endpoint, **params)
            if failure == "empty":
                return []
            if failure == "gap":
                rows.pop(2)
            if failure == "nan":
                rows[0][5] = "NaN"
            if failure == "forming":
                rows[-1][6] += 1
            if failure == "duplicate":
                rows.insert(0, rows[0])
            return rows
    with pytest.raises(ValueError):
        asyncio.run(remote_klines(Invalid(), "ETHUSDT", "1m", LEFT, LEFT + 5 * 60000, LEFT))


def test_parent_disagreement_without_child_differences_is_not_dropped(dataset):
    settings, catalog = dataset
    minutes(catalog)
    class Inconsistent(Rest):
        async def get(self, endpoint, **params):
            rows = await super().get(endpoint, **params)
            if params["interval"] == "1d":
                rows[0][5] = "1441"
            return rows
    result = check(settings, Inconsistent())
    assert not result["ready"] and result["conflicts"]
    assert result["remaining_minutes"] == []


def test_all_official_periods_are_derived_from_complete_minutes(dataset):
    settings, source = dataset
    minutes(source)
    destination = settings.catalog_dir.parent / "generated"
    event = {"source": "1m", "source_catalog": source.path,
             "policy": {"symbol": "ETHUSDT", "base_level": "1m", "intervals": list(BINANCE_KLINE_INTERVALS)},
             "start": DAY.isoformat(), "coverage_start": DAY.isoformat(), "end": (DAY + timedelta(days=1)).isoformat()}
    calculate_history_change(event, destination, completed_until=event["end"])
    result = ParquetDataCatalog(destination)
    for level in BINANCE_KLINE_INTERVALS:
        if level in {"1m", "3d", "1w", "1M"}:
            continue  # Source minutes stay in their source catalogue; long windows are incomplete.
        bars = result.bars(bar_types=[bar_type_for("ETHUSDT", level)])
        assert bars, level
        assert sum(bar.volume.as_decimal() for bar in bars) == 1440
    assert not result.bars(bar_types=[bar_type_for("ETHUSDT", "1w")])


def test_three_day_week_and_month_close_only_with_their_complete_utc_windows(dataset):
    settings, source = dataset
    source.write_data([TestInstrumentProvider.ethusdt_perp_binance()])
    rows = []
    for minute in range(31 * 1440):
        stamp = (LEFT + (minute + 1) * 60000 - 1) * 1000000
        rows.append(Bar(BarType.from_str(bar_type_for("ETHUSDT", "1m")),
                        *(Price.from_str("10.00") for _ in range(4)), Quantity.from_str("1.000"), stamp, stamp))
    source.write_data(rows)
    destination = settings.catalog_dir.parent / "calendar"
    end = datetime(2026, 9, 1, tzinfo=UTC)
    event = {"source": "1m", "source_catalog": source.path,
             "policy": {"symbol": "ETHUSDT", "base_level": "1m", "intervals": ["3d", "1w", "1M"]},
             "start": DAY.isoformat(), "coverage_start": DAY.isoformat(), "end": end.isoformat()}
    calculate_history_change(event, destination, completed_until=end.isoformat())
    result = ParquetDataCatalog(destination)
    month = result.bars(bar_types=[bar_type_for("ETHUSDT", "1M")])
    assert len(month) == 1 and month[0].volume.as_decimal() == 31 * 1440
    assert month[0].ts_event == (int(end.timestamp() * 1000) - 1) * 1000000
    weeks = result.bars(bar_types=[bar_type_for("ETHUSDT", "1w")])
    assert len(weeks) == 4 and all(bar.volume.as_decimal() == 7 * 1440 for bar in weeks)
    assert all(bar.volume.as_decimal() == 3 * 1440 for bar in result.bars(bar_types=[bar_type_for("ETHUSDT", "3d")]))


@pytest.mark.parametrize("wide", [False, True])
def test_tick_repair_merges_same_millisecond_shards_and_preserves_quotes(tmp_path, monkeypatch, wide):
    from pipeline.convert_binance_trades import convert_trade_archive
    from pipeline.models import SyncedArchive
    from tests.pipeline.convert_binance_trades_test import trade_row, write_trade_archive

    monkeypatch.setenv("NAUTILUS_RUST_CONVERTER", "off")
    instrument = TestInstrumentProvider.ethusdt_perp_binance()
    rows = [trade_row(1, LEFT, price="10.00", quantity="1.000"),
            trade_row(100, LEFT, price="11.00", quantity="1.000")]
    if wide:
        rows.append(trade_row(200, LEFT + 61000, price="12.00", quantity="1.000"))
    spec, path = write_trade_archive(tmp_path / "raw", rows)
    canonical = ParquetDataCatalog(tmp_path / "canonical")
    canonical.write_data([instrument])
    convert_trade_archive(SyncedArchive(spec, path), instrument, canonical,
                          raw_dir=tmp_path / "raw", state_dir=tmp_path / "state", bar_intervals=())
    target = ParquetDataCatalog(tmp_path / "target")
    target.write_data([instrument])
    ticks = canonical.trade_ticks()
    target.write_data([ticks[0], ticks[-1]] if wide else ticks[:1])
    assert install_tick_slice(canonical, target, "ETHUSDT", LEFT, LEFT + 60000) == 1
    assert {str(tick.trade_id) for tick in target.trade_ticks()} == ({"1", "100", "200"} if wide else {"1", "100"})
    assert len(target.trade_ticks()) == (3 if wide else 2)
    assert install_tick_slice(canonical, target, "ETHUSDT", LEFT, LEFT + 60000) == 0
    from nautilus_trader.model import NautilusDataType
    quotes = target.supplement_catalog(str(instrument.id)).query_custom_data(NautilusDataType.Custom("TradeQuote"))
    assert [str(quote.data.trade_id) for quote in quotes] == ["100"]

    event = {"source": "tick", "source_catalog": target.path,
             "policy": {"symbol": "ETHUSDT", "base_level": "tick", "intervals": ["1m"]},
             "start": DAY.isoformat(), "end": (DAY + timedelta(days=1)).isoformat()}
    calculate_history_change(event, target.path, completed_until=event["end"])
    bar = target.bars(bar_types=[bar_type_for("ETHUSDT", "1m")])[0]
    assert bar.open.as_decimal() == 10 and bar.close.as_decimal() == 11
    assert bar.volume.as_decimal() == 2


def test_cancelled_repair_waits_for_the_writer_before_releasing_its_caller(dataset, monkeypatch):
    import threading

    import pipeline.pair_repair as module
    settings, catalog = dataset
    catalog.write_data([TestInstrumentProvider.ethusdt_perp_binance()])
    entered, release = threading.Event(), threading.Event()
    async def scan(*args):
        return [], []
    def write(*args):
        entered.set()
        assert release.wait(5)
        return 0, []
    monkeypatch.setattr(module, "scan_day", scan)
    monkeypatch.setattr(module, "repair_candidates", write)
    async def scenario():
        task = asyncio.create_task(reconcile_day({"symbol": "ETHUSDT"}, settings, object(), "1m", DAY, DAY + timedelta(days=1), LEFT))
        assert await asyncio.to_thread(entered.wait, 3)
        task.cancel()
        try:
            await asyncio.sleep(0.01)
            assert not task.done()
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    asyncio.run(scenario())
