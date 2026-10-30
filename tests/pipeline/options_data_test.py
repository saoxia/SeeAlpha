"""Option REST evidence, native storage, retries and isolated deletion."""

import json
from datetime import UTC, datetime, timedelta

import pytest
from nautilus_trader.model import AggressorSide, InstrumentId, Price, Quantity, TradeId, TradeTick

from connectors.binance import options
from core.market.instruments import instrument_id_for, normalize_symbol
from pipeline.catalog import ParquetDataCatalog
from pipeline.data_management import (
    checked_request,
    dataset_inventory,
    deletion_plan,
    execute,
    preview,
)
from pipeline.models import DataRequest, PipelineSettings
from pipeline.options_data import prepare, validate
from tests.connectors.binance.options_queries_test import definition


@pytest.fixture
def context(tmp_path, monkeypatch):
    item = definition(monkeypatch)
    monkeypatch.setattr(options, "markets", lambda: [item])
    settings = PipelineSettings("futures_um", "trades", ("BTCUSDT",), ("1s", "1m", "5m", "15m"),
                                tmp_path / "raw", tmp_path / "catalog", tmp_path / "state")
    return settings, item


def recent_request():
    end = datetime.now(UTC)
    start = max(end - timedelta(minutes=15), end.replace(hour=0, minute=0, second=0, microsecond=0))
    return DataRequest(("BTC-261225-85000-C",), start, end, bar_intervals=(), market="options")


def trade(identity, stamp, price="3000.000"):
    return {"tradeId": identity, "symbol": "BTC-261225-85000-C", "time": stamp,
            "price": price, "qty": "0.01", "side": 1}


def bars_request():
    start = datetime(2026, 8, 1, tzinfo=UTC)
    return DataRequest(("BTC-261225-85000-C",), start, start + timedelta(days=1),
                       bar_intervals=("1m",), market="options")


def fake_klines(monkeypatch, request, calls):
    stamp = int(request.start.timestamp() * 1000)

    def get(endpoint, **params):
        assert endpoint == "klines"
        calls.append(params)
        if params["startTime"] > stamp:
            return []
        return [[stamp, "3000.000", "3010.000", "2990.000", "3005.000", "0.03", stamp + 59999],
                [stamp + 60000, "3005.000", "3010.000", "2990.000", "3005.000", "0.00", stamp + 119999]]

    monkeypatch.setattr(options, "get", get)


def test_option_model_and_perpetual_identity_are_separate():
    assert normalize_symbol("btc-261225-85000-c", "options") == "BTC-261225-85000-C"
    assert instrument_id_for("BTC-261225-85000-C", "options") == "BTC-261225-85000-C.BINANCE"
    assert instrument_id_for("BTCUSDC") == "BTCUSDC-PERP.BINANCE"
    assert bars_request().data_type == "klines"
    for symbol in ["BTC-260231-85000-C", "BTC-261225-0-C", "../../BTC-261225-85000-C", "BTC-261225-85000-X"]:
        with pytest.raises(ValueError):
            normalize_symbol(symbol, "options")


def test_recent_tick_capture_deduplicates_and_does_not_claim_full_history(context, monkeypatch):
    settings, _item = context
    request = recent_request()
    stamp = int(request.start.timestamp() * 1000) + 1000
    packet = [trade(1, stamp), trade(2, stamp), trade(1, stamp)]
    monkeypatch.setattr(options, "get", lambda *args, **kwargs: packet)
    first = prepare(request, settings)
    assert first["ready"] and first["records_written"] == 2
    assert not first["historical_tick_complete"]
    assert prepare(request, settings)["records_written"] == 0
    assert validate(request, settings)["ready"]
    data = preview(settings, request.symbols[0], request.start, request.end, market="options")
    assert [row["trade_id"] for row in data["records"]] == ["1", "2"]
    assert data["records"][0]["aggressor_side"] == "BUY"
    packet.append(trade(3, stamp + 1000))
    assert prepare(request, settings)["records_written"] == 1
    assert validate(request, settings)["ready"]
    items = dataset_inventory(settings, all_levels=True)
    assert [(row["product"], row["level"]) for row in items] == [("options", "tick")]
    assert items[0]["quality"] == "partial_recent_trades"
    assert items[0]["contract"]["multiplier"] == "10"


def test_option_bars_source_closed_times_retry_and_delete_isolation(context, monkeypatch, tmp_path):
    settings, _item = context
    request, calls = bars_request(), []
    fake_klines(monkeypatch, request, calls)
    result = execute("prepare", request, settings, tmp_path / "unused")
    assert result["ready"] and result["records_written"] == 2
    assert not result["historical_tick_complete"]
    assert validate(request, settings)["ready"]
    assert len(calls) == 2
    assert prepare(request, settings)["records_written"] == 0
    assert len(calls) == 2
    data = preview(settings, request.symbols[0], request.start, request.start + timedelta(minutes=2),
                   interval="1m", market="options")
    assert int(data["records"][0]["ts_event_ns"]) == int(request.start.timestamp()) * 1_000_000_000 + 60_000_000_000 - 1
    catalog = ParquetDataCatalog(settings.catalog_dir)
    catalog.write_data([TradeTick(InstrumentId.from_str("BTCUSDT-PERP.BINANCE"), Price.from_str("85000.00"),
                                  Quantity.from_str("0.01"), AggressorSide.BUY, TradeId("99"),
                                  int(request.start.timestamp()) * 1_000_000_000, 0)])
    plan = deletion_plan(request, settings)
    assert plan["file_count"] == 1
    execute("delete", request, settings, tmp_path / "unused", expected_snapshot=plan["snapshot"])
    assert len(catalog.trade_ticks(instrument_ids=["BTCUSDT-PERP.BINANCE"])) == 1
    monkeypatch.setattr(options, "get", lambda *args, **kwargs: pytest.fail("should restore from local evidence"))
    monkeypatch.setattr(options, "markets", lambda: pytest.fail("local instrument is retained"))
    assert prepare(request, settings)["ready"]
    plan = deletion_plan(request, settings, delete_archives=True)
    execute("delete", request, settings, tmp_path / "unused", expected_snapshot=plan["snapshot"], delete_archives=True)
    assert not list(settings.raw_dir.rglob("*.json"))
    assert len(catalog.trade_ticks(instrument_ids=["BTCUSDT-PERP.BINANCE"])) == 1


def test_interrupted_install_replays_immutable_sources(context, monkeypatch):
    import os

    settings, _item = context
    request, calls = bars_request(), []
    fake_klines(monkeypatch, request, calls)
    original = os.link

    def interrupted(source, destination):
        if ".options-stage-" in str(source):
            raise OSError("fixture interruption")
        return original(source, destination)

    with monkeypatch.context() as patch:
        patch.setattr(os, "link", interrupted)
        with pytest.raises(OSError, match="interruption"):
            prepare(request, settings)
    assert list(settings.raw_dir.rglob("*.json"))
    assert not validate(request, settings)["ready"]
    count = len(calls)
    assert prepare(request, settings)["ready"]
    assert len(calls) == count
    assert validate(request, settings)["ready"]


def test_source_corruption_conflict_and_precision_do_not_overwrite(context, monkeypatch):
    settings, _item = context
    request = recent_request()
    stamp = int(request.start.timestamp() * 1000) + 1000
    packet = [trade(1, stamp)]
    monkeypatch.setattr(options, "get", lambda *args, **kwargs: packet)
    prepare(request, settings)
    packet[0]["price"] = "3000.0001"
    with pytest.raises(ValueError, match="精度"):
        prepare(request, settings)
    packet[0]["price"] = "3010.000"
    with pytest.raises(ValueError, match="冲突"):
        prepare(request, settings)
    assert not validate(request, settings)["ready"]
    source = next(settings.raw_dir.rglob("*.json"))
    value = json.loads(source.read_text())
    value["fetched_ms"] += 1
    source.write_text(json.dumps(value))
    assert not validate(request, settings)["ready"]
    assert preview(settings, request.symbols[0], request.start, request.end, market="options")["records"][0]["price"] == "3000.000"


def test_unsupported_period_and_historical_tick_download_are_rejected(context):
    settings, _item = context
    request = bars_request()
    with pytest.raises(ValueError, match="1m"):
        checked_request(request.symbols[0], request.start, request.end, ["1s"], settings, market="options")
    old = DataRequest(request.symbols, request.start, request.end, bar_intervals=(), market="options")
    with pytest.raises(ValueError, match="近期"):
        prepare(old, settings)


def test_empty_response_and_nonadvancing_pages_never_create_fake_data(context, monkeypatch):
    settings, _item = context
    request = bars_request()
    monkeypatch.setattr(options, "get", lambda *args, **kwargs: [])
    assert not prepare(request, settings)["ready"]
    assert not settings.catalog_dir.exists()
    assert not list(settings.raw_dir.rglob("*.json"))
    stamp = int(request.start.timestamp() * 1000)
    monkeypatch.setattr(options, "get", lambda *args, **kwargs:
                        [[stamp, "3000.000", "3000.000", "3000.000", "3000.000", "0.01", stamp + 59999]])
    with pytest.raises(ValueError, match="分页"):
        prepare(request, settings)
    assert not settings.catalog_dir.exists()


def test_worker_entry_point_carries_option_product(context, monkeypatch, tmp_path):
    import sys

    from pipeline import data_management

    settings, _item = context
    request, calls = bars_request(), []
    fake_klines(monkeypatch, request, calls)
    monkeypatch.setattr(data_management, "load_settings", lambda: settings)
    monkeypatch.setattr(data_management, "PROJECT_ROOT", tmp_path)
    payload = {"symbol": request.symbols[0], "product": "options", "start": request.start.isoformat(),
               "end": request.end.isoformat(), "intervals": ["1m"]}
    identity = "f" * 32
    monkeypatch.setattr(sys, "argv", ["data_management", "prepare", "--request", json.dumps(payload),
                                      "--job-id", identity])
    data_management.main()
    result = json.loads((tmp_path / ".local/state/api/data_jobs" / f"{identity}.json").read_text())
    assert result["ready"] and result["scope"] == "options_exchange_klines"
    assert dataset_inventory(settings, all_levels=True)[0]["product"] == "options"


def test_recent_capture_keeps_returned_trades_with_older_observed_dates(context, monkeypatch):
    settings, _item = context
    request = recent_request()
    yesterday = request.start.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1)
    first = int(yesterday.timestamp() * 1000) + 1000
    last = int(request.start.timestamp() * 1000) + 1000
    packet = [trade(10, first), trade(11, last)]
    monkeypatch.setattr(options, "get", lambda *args, **kwargs: packet)
    result = prepare(request, settings)
    assert result["records_written"] == 2
    assert result["observed_start"].startswith(yesterday.date().isoformat())
    full = DataRequest(request.symbols, yesterday, yesterday + timedelta(days=2),
                       bar_intervals=(), market="options")
    assert validate(full, settings)["ready"]
    assert not result["historical_tick_complete"]


def test_disappearing_storage_is_not_recreated(context, monkeypatch):
    settings, _item = context
    request = bars_request()
    root = settings.raw_dir.parent
    moved = root.with_name(root.name + "-detached")
    stamp = int(request.start.timestamp() * 1000)

    def lost_volume(endpoint, **params):
        if root.exists():
            root.rename(moved)
        if params["startTime"] > stamp:
            return []
        return [[stamp, "3000.000", "3000.000", "3000.000", "3000.000", "0.01", stamp + 59999]]

    monkeypatch.setattr(options, "get", lost_volume)
    try:
        with pytest.raises(OSError, match="存储"):
            prepare(request, settings)
        assert not root.exists()
    finally:
        moved.rename(root)
    assert not list(settings.raw_dir.rglob("*.json"))


def test_local_validation_checks_contract_multiplier_and_precision(context, monkeypatch):
    from nautilus_trader.model import CryptoOption

    settings, _item = context
    request = recent_request()
    stamp = int(request.start.timestamp() * 1000) + 1000
    monkeypatch.setattr(options, "get", lambda *args, **kwargs: [trade(1, stamp)])
    assert prepare(request, settings)["ready"]
    catalog = ParquetDataCatalog(settings.catalog_dir)
    original = catalog.instruments()[0].to_dict()
    original["multiplier"] = "1"
    original["ts_init"] += 1
    catalog.write_data([CryptoOption.from_dict(original)])
    result = validate(request, settings)
    assert not result["ready"]
    assert any("合约单位" in error for error in result["errors"])
