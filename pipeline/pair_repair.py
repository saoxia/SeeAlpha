"""Hierarchical history checks and additive repairs inside the existing Pipeline."""

import asyncio
import os
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

import pyarrow as pa
import pyarrow.parquet as pq
from nautilus_trader.model import Bar, BarType, NautilusDataType, Price, Quantity

from core.market.instruments import bar_type_for, bar_window, instrument_id_for
from pipeline.catalog import ParquetDataCatalog
from pipeline.models import DataRequest
from pipeline.prepare_data import ensure_instruments, prepare_data

SCAN_PATH = ("1d", "4h", "1h", "15m", "5m", "1m")
FIELDS = ("open", "high", "low", "close", "volume")


def local_klines(catalog, symbol, source, level, start_ms, end_ms):
    """Aggregate actual source rows, never infer contents from file coverage."""
    identifier = instrument_id_for(symbol) if source == "tick" else bar_type_for(symbol, source)
    data_type = NautilusDataType.TradeTick if source == "tick" else NautilusDataType.Bar
    step = 1000 if source == "1s" else 60000
    stream = catalog.native.query_data_arrow_stream(
        data_type, [identifier], start_ms * 1000000, end_ms * 1000000 - 1, display=False)
    reader = pa.RecordBatchReader._import_from_c_capsule(stream)
    buckets = {}
    try:
        for batch in reader:
            for offset in range(0, batch.num_rows, 1000):
                piece = batch.slice(offset, 1000)
                stamps = piece.column("ts_event").cast(pa.int64()).to_pylist()
                for stamp, row in zip(stamps, piece.to_pylist(), strict=True):
                    opening = stamp // 1000000 if source == "tick" else stamp // 1000000 + 1 - step
                    left, _ = bar_window(level, opening)
                    values = ([Decimal(str(row["price"]))] * 4 + [Decimal(str(row["size"]))]
                              if source == "tick" else [Decimal(str(row[name])) for name in FIELDS])
                    key = (stamp, int(row["trade_id"])) if source == "tick" else (stamp, 0)
                    if left not in buckets:
                        buckets[left] = {"values": values, "first": key, "last": key, "count": 1}
                    else:
                        bucket = buckets[left]
                        old = bucket["values"]
                        if key < bucket["first"]:
                            old[0], bucket["first"] = values[0], key
                        if key > bucket["last"]:
                            old[3], bucket["last"] = values[3], key
                        old[1], old[2], old[4] = max(old[1], values[1]), min(old[2], values[2]), old[4] + values[4]
                        bucket["count"] += 1
    finally:
        reader.close()
    return buckets


async def remote_klines(rest, symbol, level, start_ms, end_ms, available_ms):
    """Require a complete, ordered closed response, including every REST page."""
    result = []
    cursor = start_ms
    while cursor < end_ms:
        rows = await rest.get("klines", symbol=symbol, interval=level,
                              startTime=cursor, endTime=end_ms - 1, limit=1000)
        if not rows:
            raise ValueError(f"{level} 对照来源为空；不能声明核验通过")
        for row in rows:
            left, right = int(row[0]), int(row[6]) + 1
            allowed_first = bar_window(level, available_ms)[0]
            if (left != cursor and not (not result and cursor < available_ms and left == allowed_first)
                    or bar_window(level, left) != (left, right) or right > end_ms or right <= cursor):
                raise ValueError(f"{level} 对照来源缺口、乱序或周期边界无效")
            values = [Decimal(str(row[i])) for i in range(1, 6)]
            opening, high, low, close, volume = values
            count = int(row[8])
            if (not all(value.is_finite() for value in values) or min(values[:4]) <= 0 or volume < 0
                    or high < max(opening, close) or low > min(opening, close) or low > high or count < 0):
                raise ValueError("交易所对照 K 线数值无效")
            result.append({"start_ms": left, "end_ms": right, "values": values, "count": count})
            cursor = right
    return result


def differs(local, remote, source, available_ms):
    if local is None:
        return not (source in {"tick", "1s"} and remote["count"] == 0 and remote["values"][4] == 0)
    if local["values"] != remote["values"]:
        return True
    if source == "tick":
        return local["count"] != remote["count"]
    if source == "1m":
        # Missing zero-volume minutes must not disappear behind equal OHLCV.
        begin = max(remote["start_ms"], available_ms // 60000 * 60000)
        return local["count"] != (remote["end_ms"] - begin) // 60000
    return False  # Legacy seconds have no fabricated buckets for no-trade seconds.


async def scan_day(rest, catalog, symbol, source, start_ms, end_ms, available_ms):
    """Only mismatching branches descend; retain inconsistent parent evidence."""
    candidates, unresolved = [], []

    async def descend(level_index, left, right):
        level = SCAN_PATH[level_index]
        remote = await remote_klines(rest, symbol, level, left, right, available_ms)
        local = await asyncio.to_thread(local_klines, catalog, symbol, source, level, left, right)
        found = False
        for item in remote:
            if not differs(local.get(item["start_ms"]), item, source, available_ms):
                continue
            found = True
            if level == "1m":
                candidates.append({**item, "local": local.get(item["start_ms"])})
            elif not await descend(level_index + 1, item["start_ms"], item["end_ms"]):
                unresolved.append({"start_ms": item["start_ms"], "interval": level,
                                   "reason": "大周期与子周期对照不一致"})
        return found

    await descend(0, start_ms, end_ms)
    return candidates, unresolved


def install_tick_slice(canonical, catalog, symbol, start_ms, end_ms):
    """Add confirmed identities; same-ID conflicts never replace existing evidence."""
    identifier = instrument_id_for(symbol)
    expected = canonical.trade_ticks(instrument_ids=[identifier], start=start_ms * 1000000, end=end_ms * 1000000 - 1)
    actual = catalog.trade_ticks(instrument_ids=[identifier], start=start_ms * 1000000, end=end_ms * 1000000 - 1)
    existing = {str(tick.trade_id): tick for tick in actual}
    for tick in expected:
        old = existing.get(str(tick.trade_id))
        if old is not None and any(getattr(old, name) != getattr(tick, name)
                                   for name in ("ts_event", "price", "size", "aggressor_side")):
            raise ValueError("历史成交同编号内容冲突；保留原始数据")
    missing = [tick for tick in expected if str(tick.trade_id) not in existing]
    if not missing:
        return 0
    with TemporaryDirectory(prefix=".pair-ticks-", dir=catalog.path) as directory:
        staged = ParquetDataCatalog(directory)
        staged.write_data(missing)
        identities = {str(tick.trade_id) for tick in missing}
        # Install quote supplements first. Interrupted retries merge by identity,
        # so a receipt never causes a duplicate supplement or missing quote field.
        quotes = canonical.supplement_catalog(identifier).query_custom_data(
            NautilusDataType.Custom("TradeQuote"), start=start_ms * 1000000, end=end_ms * 1000000 - 1)
        selected = [quote for quote in quotes if str(quote.data.trade_id) in identities]
        if {str(quote.data.trade_id) for quote in selected} != identities:
            raise ValueError("成交来源缺少 quote 补充数据；不写入 Tick")
        staged.supplement_catalog(identifier).write_custom_data(selected)
        paths = sorted(Path(directory).rglob("*.parquet"), key=lambda path: "supplements" not in path.parts)
        for path in paths:
            destination = Path(catalog.path) / path.relative_to(directory)
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                # A one-millisecond shard may already exist. Merge its identities
                # into the staged shard and atomically replace only this file.
                old, fresh = pq.read_table(destination), pq.read_table(path)
                old_rows = {str(row["trade_id"]): row for row in old.to_pylist()}
                rows = list(old_rows.values())
                for row in fresh.to_pylist():
                    identity = str(row["trade_id"])
                    if identity in old_rows and old_rows[identity] != row:
                        raise ValueError("成交分片内容冲突；保留原文件")
                    if identity not in old_rows:
                        rows.append(row)
                rows.sort(key=lambda row: (row["ts_event"], int(row["trade_id"])))
                pq.write_table(pa.Table.from_pylist(rows, schema=old.schema), path)
            with path.open("rb") as stream:
                os.fsync(stream.fileno())
            if destination.exists():
                os.replace(path, destination)
            else:
                os.link(path, destination)
    return len(missing)


def repair_candidates(policy, settings, catalog, source, candidates, day, instrument):
    """Use official minutes or checked daily trades; never reverse bars into trades."""
    from pipeline.pair_data import install_bars

    written, conflicts = 0, []
    if source == "1m":
        bars = []
        for item in candidates:
            values = item["values"]
            price_step = Decimal(1).scaleb(-instrument.price_precision)
            size_step = Decimal(1).scaleb(-instrument.size_precision)
            if (any(value != value.quantize(price_step) for value in values[:4])
                    or values[4] != values[4].quantize(size_step)):
                raise ValueError("补齐来源精度与合约不一致")
            stamp = (item["end_ms"] - 1) * 1000000
            existing = catalog.bars(bar_types=[bar_type_for(policy["symbol"], "1m")], start=stamp, end=stamp)
            if existing:
                if any(getattr(existing[0], name).as_decimal() != value for name, value in zip(FIELDS, values, strict=True)):
                    conflicts.append({"start_ms": item["start_ms"], "interval": "1m", "reason": "已有分钟来源值冲突"})
                continue
            bars.append(Bar(BarType.from_str(bar_type_for(policy["symbol"], "1m")),
                            *(Price.from_str(str(value)) for value in values[:4]), Quantity.from_str(str(values[4])), stamp, stamp))
        install_bars(catalog, bars)
        return len(bars), conflicts
    if not candidates:
        return 0, conflicts
    with TemporaryDirectory(prefix=".pair-source-", dir=catalog.path) as directory:
        staging = Path(directory)
        canonical = ParquetDataCatalog(staging / "catalog")
        canonical.write_data([instrument])
        intervals = ("1s",) if source == "1s" else ()
        prepare_data(DataRequest((policy["symbol"],), day, day + timedelta(days=1), bar_intervals=intervals),
                     settings=replace(settings, catalog_dir=Path(canonical.path), state_dir=staging / "state",
                                      symbols=(policy["symbol"],), intervals=intervals))
        for item in candidates:
            if source == "tick":
                written += install_tick_slice(canonical, catalog, policy["symbol"], item["start_ms"], item["end_ms"])
            else:
                expected = canonical.bars(bar_types=[bar_type_for(policy["symbol"], "1s")],
                                          start=item["start_ms"] * 1000000, end=item["end_ms"] * 1000000 - 1)
                actual = {bar.ts_event: bar for bar in catalog.bars(bar_types=[bar_type_for(policy["symbol"], "1s")],
                           start=item["start_ms"] * 1000000, end=item["end_ms"] * 1000000 - 1)}
                if any(bar.ts_event in actual and any(getattr(bar, name) != getattr(actual[bar.ts_event], name)
                                                     for name in FIELDS) for bar in expected):
                    conflicts.append({"start_ms": item["start_ms"], "interval": "1s", "reason": "已有秒来源值冲突"})
                    continue
                fresh = [bar for bar in expected if bar.ts_event not in actual]
                install_bars(catalog, fresh)
                written += len(fresh)
    return written, conflicts


async def reconcile_day(policy, settings, rest, source, day, finish, available_ms):
    catalog = ParquetDataCatalog(settings.catalog_dir)
    instrument = (await asyncio.to_thread(ensure_instruments, catalog, (policy["symbol"],)))[policy["symbol"]]
    start_ms, end_ms = int(day.timestamp() * 1000), int(finish.timestamp() * 1000)
    candidates, unresolved = await scan_day(rest, catalog, policy["symbol"], source, start_ms, end_ms, available_ms)
    writing = asyncio.create_task(asyncio.to_thread(repair_candidates, policy, settings, catalog, source, candidates, day, instrument))
    try:
        written, conflicts = await asyncio.shield(writing)
    except asyncio.CancelledError:
        # Keep the caller's file lease until the worker has actually stopped writing.
        await writing
        raise
    remaining, after_errors = (await scan_day(rest, catalog, policy["symbol"], source, start_ms, end_ms, available_ms)
                               if written else (candidates, []))
    return {"ready": not remaining and not unresolved and not conflicts and not after_errors,
            "checked_at": datetime.now(UTC).isoformat(), "records_written": written,
            "candidates": len(candidates), "remaining_minutes": [item["start_ms"] for item in remaining],
            "conflicts": conflicts + unresolved + after_errors,
            "differences": [{"start_ms": item["start_ms"], "expected": dict(zip(FIELDS, map(str, item["values"]), strict=True)),
                             "expected_trade_count": item["count"],
                             "observed": dict(zip(FIELDS, map(str, item["local"]["values"]), strict=True)) if item["local"] else None,
                             "observed_source_rows": item["local"]["count"] if item["local"] else 0}
                            for item in remaining],
            "scope": "hierarchical_ohlcv_and_available_trade_count"}
