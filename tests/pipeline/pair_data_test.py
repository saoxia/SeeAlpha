"""Pair backfill uses calendar windows and respects existing Catalog ranges."""

import asyncio
from datetime import UTC, datetime, timedelta

from nautilus_trader.model import Bar

from nautilus_support import TestInstrumentProvider
from pipeline.catalog import ParquetDataCatalog
from pipeline.models import PipelineSettings
from pipeline.pair_data import backfill, history_bounds


def test_history_modes_use_retained_anchor_and_explicit_start(tmp_path):
    catalog = ParquetDataCatalog(tmp_path / "catalog")
    now = datetime(2026, 10, 29, tzinfo=UTC)
    policy = {"symbol": "ETHUSDT", "base_level": "1m", "history_mode": "duration", "history_days": 30}
    assert history_bounds(policy, catalog, now) == (now - timedelta(days=30), now)
    assert history_bounds({**policy, "history_mode": "all"}, catalog, now) == (None, now)
    first = datetime(2026, 8, 1, tzinfo=UTC)
    assert history_bounds({**policy, "history_mode": "start", "history_start": first.isoformat()}, catalog, now) == (first, now)


def test_exchange_history_installs_selected_bars_without_ticks_and_retries_idempotently(tmp_path, monkeypatch):
    import pipeline.pair_data as module
    (tmp_path / "data").mkdir()
    monkeypatch.setattr(module, "PROJECT_ROOT", tmp_path)
    settings = PipelineSettings("futures_um", "trades", ("ETHUSDT",), ("1m",), tmp_path / "data/raw", tmp_path / "data/catalog", tmp_path / "data/state")
    catalog = ParquetDataCatalog(settings.catalog_dir)
    catalog.write_data([TestInstrumentProvider.ethusdt_perp_binance()])
    first = datetime(2026, 10, 28, tzinfo=UTC)
    left = int(first.timestamp() * 1000)

    class Rest:
        async def get(self, endpoint, **params):
            assert endpoint == "klines"
            if params.get("limit") == 1:
                return [[left, "10", "10", "10", "10", "2", left + 59999]]
            assert params["interval"] == "1d"
            return [[left, "10", "12", "9", "11", "2", left + 86400000 - 1]]

    policy = {"symbol": "ETHUSDT", "product": "futures_um", "base_level": "1d", "intervals": [], "history_mode": "all"}
    result = asyncio.run(backfill(policy, settings, rest=Rest(), now=first + timedelta(days=1)))
    assert result["ready"] and result["records_written"] == 1
    assert len(catalog.bars()) == 1
    assert not catalog.trade_ticks()
    repeated = asyncio.run(backfill(policy, settings, rest=Rest(), now=first + timedelta(days=1)))
    assert repeated["records_written"] == 0 and len(catalog.bars()) == 1
    start, _ = history_bounds({**policy, "history_mode": "duration", "history_days": 3}, catalog, first + timedelta(days=1))
    assert start == first - timedelta(days=3)
    assert catalog.get_intervals(Bar)


def test_duration_uses_retained_realtime_base_and_calendar_open(tmp_path):
    now = datetime(2026, 10, 29, tzinfo=UTC)
    first = datetime(2026, 8, 1, tzinfo=UTC)
    policy = {"symbol": "ETHUSDT", "base_level": "1M", "history_mode": "duration", "history_days": 30}
    # Catalog event convention points to the close; the anchor is the opening month.
    closed = datetime(2026, 8, 31, 23, 59, 59, tzinfo=UTC)
    class Catalog:
        def get_intervals(self, *args):
            stamp = int(closed.timestamp() * 1e9)
            return [(stamp, stamp)]
    start, _ = history_bounds(policy, Catalog(), now)
    assert start == first - timedelta(days=30)
    retained = int(datetime(2026, 7, 15, tzinfo=UTC).timestamp() * 1e9)
    start, _ = history_bounds({**policy, "base_level": "1m"}, None, now, retained_start_ns=retained)
    assert start == datetime(2026, 6, 15, tzinfo=UTC)


def test_history_calculation_derives_complete_windows_and_preserves_conflicts(tmp_path):
    from nautilus_trader.model import BarType, Price, Quantity

    from core.market.instruments import bar_type_for
    from pipeline.pair_data import calculate_history_change
    source = ParquetDataCatalog(tmp_path / "source")
    source.write_data([TestInstrumentProvider.ethusdt_perp_binance()])
    start = datetime(2026, 10, 28, tzinfo=UTC)
    left = int(start.timestamp() * 1000)
    bars = [Bar(BarType.from_str(bar_type_for("ETHUSDT", "1m")), *(Price.from_str(v) for v in ("10", "12", "9", "11")),
                Quantity.from_str("2"), (left + (i + 1) * 60000 - 1) * 1000000,
                (left + (i + 1) * 60000 - 1) * 1000000) for i in range(120)]
    source.write_data(bars)
    event = {"policy": {"symbol": "ETHUSDT", "base_level": "1m", "intervals": ["1h", "1d"]},
             "source": "1m", "source_catalog": source.path, "start": start.isoformat(),
             "coverage_start": start.isoformat(), "end": (start + timedelta(days=1)).isoformat()}
    destination = tmp_path / "destination"
    assert calculate_history_change(event, destination, completed_until=event["end"]) == 2
    catalog = ParquetDataCatalog(destination)
    result = catalog.bars()
    assert len(result) == 2 and all(bar.volume.as_decimal() == 120 for bar in result)
    assert calculate_history_change(event, destination, completed_until=event["end"]) == 0
    altered = ParquetDataCatalog(tmp_path / "altered")
    altered.write_data([TestInstrumentProvider.ethusdt_perp_binance()])
    altered.write_data([Bar(bar.bar_type, bar.open, Price.from_str("13"), bar.low, bar.close, bar.volume,
                            bar.ts_event, bar.ts_init) for bar in bars])
    import pytest
    with pytest.raises(ValueError, match="冲突"):
        calculate_history_change({**event, "source_catalog": altered.path}, destination, completed_until=event["end"])
    assert catalog.bars() == result


def test_history_restart_replays_unfinished_day_and_keeps_fixed_duration_anchor(tmp_path, monkeypatch):
    import json

    import pipeline.pair_data as module
    from pipeline.convert_binance_bars import write_record
    root = tmp_path / "data/history"
    root.mkdir(parents=True)
    monkeypatch.setattr(module, "PROJECT_ROOT", tmp_path)
    settings = PipelineSettings("futures_um", "trades", ("ETHUSDT",), ("1m",), tmp_path / "data/raw", tmp_path / "data/catalog", tmp_path / "data/state")
    now = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    first = now - timedelta(days=1)
    policy = {"symbol": "ETHUSDT", "product": "futures_um", "base_level": "1m", "intervals": ["1h"],
              "history_mode": "duration", "history_days": 1}
    state = {"configuration": module.history_configuration(policy), "start": first.isoformat(), "next_day": first.isoformat(),
             "available_ms": int(first.timestamp() * 1000), "verification_version": 1}
    write_record(root / "checkpoint.json", state)
    calls = []
    async def unavailable(*args, **kwargs):
        calls.append(args[0])
        raise OSError("offline")
    async def stop(_seconds):
        raise asyncio.CancelledError
    monkeypatch.setattr("pipeline.pair_repair.reconcile_day", unavailable)
    monkeypatch.setattr(module.asyncio, "sleep", stop)
    import pytest
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(module.serve_history(policy, settings, root, rest=object()))
    assert json.loads((root / "checkpoint.json").read_text()) == state
    async def recovered(*args, **kwargs):
        calls.append(args[0])
        return {"ready": True, "checked_at": "now", "records_written": 0}
    monkeypatch.setattr("pipeline.pair_repair.reconcile_day", recovered)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(module.serve_history(policy, settings, root, rest=object()))
    restored = json.loads((root / "checkpoint.json").read_text())
    assert restored["start"] == first.isoformat() and restored["next_day"] == first.isoformat()
    assert restored["next_scan_at"] > 0  # A completed sweep schedules a later rescan.
    assert calls[0]["symbol"] == calls[1]["symbol"] == "ETHUSDT"
    assert json.loads(next((root / "changes").glob("*.json")).read_text())["state"] == "ready"


def test_failed_source_page_install_leaves_no_partial_public_file(tmp_path, monkeypatch):
    import pytest

    import pipeline.pair_data as module
    catalog = ParquetDataCatalog(tmp_path / 'catalog')
    class InterruptedWriter:
        def __init__(self, path):
            self.path = path
        def write_data(self, _bars):
            from pathlib import Path
            partial = Path(self.path) / 'data/bars/interrupted.parquet'
            partial.parent.mkdir(parents=True)
            partial.write_bytes(b'incomplete page')
            raise OSError('writer interrupted')
    monkeypatch.setattr(module, 'ParquetDataCatalog', InterruptedWriter)
    with pytest.raises(OSError, match='interrupted'):
        module.install_bars(catalog, [object()])
    from pathlib import Path
    assert not list(Path(catalog.path).rglob('*.parquet'))


def test_explicit_history_dates_are_aligned_to_utc_days():
    from datetime import timezone
    now = datetime(2026, 10, 30, 3, 45, tzinfo=timezone(timedelta(hours=8)))
    policy = {"symbol": "ETHUSDT", "base_level": "1m", "history_mode": "start",
              "history_start": "2026-08-02T03:45:12+08:00"}
    assert history_bounds(policy, None, now) == (
        datetime(2026, 8, 1, tzinfo=UTC), datetime(2026, 10, 29, tzinfo=UTC))
