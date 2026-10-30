"""Continuous pair source backfill and legacy explicit entry point; Catalog stays owned here."""

import argparse
import asyncio
import fcntl
import hashlib
import json
import os
import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

from nautilus_trader.model import Bar, BarType, Price, Quantity, TradeTick

from connectors.binance.pair_data import PairREST
from core.market.instruments import bar_type_for, bar_window, instrument_id_for
from core.paths import PROJECT_ROOT
from pipeline.catalog import ParquetDataCatalog
from pipeline.config import load_settings
from pipeline.convert_binance_bars import write_record
from pipeline.convert_binance_trades import (
    aggregate_parts_to_bars,
    aggregate_trade_chunk,
    iter_trade_archive,
)
from pipeline.models import DataRequest
from pipeline.prepare_data import ensure_instruments, prepare_data
from pipeline.sync_binance_batch import plan_daily_archives, sync_archives


def history_bounds(policy, catalog, now, *, retained_start_ns=None):
    """Use the saved UTC day; retain mode resolution for legacy CLI callers."""
    end = now.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    if policy["history_mode"] == "start":
        start = datetime.fromisoformat(policy["history_start"]).astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        return start, end
    if policy["history_mode"] != "duration":
        return None, end
    base = policy["base_level"] if policy["base_level"] != "auto" else "1m"
    ranges = catalog.get_intervals(TradeTick if base == "tick" else Bar,
                                 instrument_id_for(policy["symbol"]) if base == "tick" else bar_type_for(policy["symbol"], base)) if catalog else []
    starts = [int(a) // 1000000 for a, _ in ranges]
    if retained_start_ns is not None:
        starts.append(retained_start_ns // 1000000)
    if base != "tick":
        starts = [bar_window(base, stamp)[0] for stamp in starts]
    anchor = min((datetime.fromtimestamp(stamp / 1000, UTC).replace(hour=0, minute=0, second=0, microsecond=0) for stamp in starts), default=end)
    return anchor - timedelta(days=policy["history_days"]), end


def missing_bars(catalog, bars):
    if not bars:
        return []
    ranges = catalog.get_intervals(Bar, str(bars[0].bar_type))
    return [bar for bar in bars if not any(int(a) <= bar.ts_event <= int(b) for a, b in ranges)]


def install_bars(catalog, bars):
    """Install complete native files; never expose a half-written history page."""
    if not bars:
        return
    with TemporaryDirectory(prefix=".pair-bars-", dir=catalog.path) as staging:
        staged = ParquetDataCatalog(staging)
        staged.write_data(bars)
        for path in Path(staging).rglob("*.parquet"):
            installed = Path(catalog.path) / path.relative_to(staging)
            installed.parent.mkdir(parents=True, exist_ok=True)
            if installed.exists():
                raise ValueError("行情文件已变化；保留来源等待重新核对")
            with path.open("rb") as stream:
                os.fsync(stream.fileno())
            os.link(path, installed)


async def backfill(policy, settings, *, rest=None, now=None):
    if not (PROJECT_ROOT / "data").is_dir():
        raise OSError("数据挂载不可用")
    if policy["product"] != "futures_um":
        raise ValueError("此追加入口当前仅支持 U 本位永续；期权请使用已有历史接口")
    rest = rest or PairREST(symbols=(policy["symbol"],))
    catalog = ParquetDataCatalog(settings.catalog_dir)
    start, end = history_bounds(policy, catalog, now or datetime.now(UTC))
    earliest = await rest.get("klines", symbol=policy["symbol"], interval="1m", startTime=0, limit=1)
    if not earliest:
        raise ValueError("交易所没有可用历史，未标记完成")
    available = datetime.fromtimestamp(int(earliest[0][0]) / 1000, UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    start = max(start or available, available)
    if policy.get("resolved_end"):
        end = datetime.fromisoformat(policy["resolved_end"])
        start = datetime.fromisoformat(policy["resolved_start"]) if policy.get("resolved_start") else available
        start = max(start, available)
    base = policy["base_level"] if policy["base_level"] != "auto" else "1m"
    levels = list(dict.fromkeys(([base] if base != "tick" else []) + policy["intervals"]))
    instruments = await asyncio.to_thread(ensure_instruments, catalog, (policy["symbol"],))
    instrument = instruments[policy["symbol"]]
    written = 0
    day = start
    while day < end:
        finish = min(day + timedelta(days=1), end)
        print(f"追加 {policy['symbol']} {day.date()}，仅补缺失", flush=True)
        if base == "tick":
            request = DataRequest((policy["symbol"],), day, finish, bar_intervals=("1s",) if "1s" in levels else ())
            await asyncio.to_thread(prepare_data, request, settings=replace(settings, symbols=request.symbols, intervals=("1s",) if "1s" in levels else ()))
        if base == "1s" or "1s" in levels and base != "tick":
            request = DataRequest((policy["symbol"],), day, finish, bar_intervals=("1s",))
            archives = await asyncio.to_thread(sync_archives, plan_daily_archives(request), settings.raw_dir)
            for archive in archives:
                parts = [aggregate_trade_chunk(chunk, "1s") for chunk in iter_trade_archive(archive.path)]
                bars = aggregate_parts_to_bars(parts, instrument, "1s", bar_type_for(policy["symbol"], "1s"))
                bars = missing_bars(catalog, bars)
                install_bars(catalog, bars)
                written += len(bars)
        for level in levels:
            if level == "1s":
                continue
            # Week/month bars may cross days: request the whole native window and
            # install only complete candles. Repeated days are idempotent.
            cursor, _ = bar_window(level, int(day.timestamp() * 1000))
            stop = int(finish.timestamp() * 1000)
            while cursor < stop:
                rows = await rest.get("klines", symbol=policy["symbol"], interval=level,
                                      startTime=cursor, endTime=stop - 1, limit=1000)
                if not rows:
                    raise ValueError(f"{level} 来源为空，进度未跳过此页")
                bars = []
                expected = int(rows[0][0])
                if expected > cursor >= int(earliest[0][0]):
                    raise ValueError(f"{level} 来源缺少请求起点，保留已完成数据")
                for row in rows:
                    if int(row[0]) != expected:
                        raise ValueError(f"{level} 分页内部存在缺口")
                    expected = int(row[6]) + 1
                    left, right = int(row[0]), int(row[6]) + 1
                    if bar_window(level, left) != (left, right):
                        raise ValueError("交易所周期边界无效")
                    if left < int(start.timestamp() * 1000) or right > int(end.timestamp() * 1000):
                        continue
                    bars.append(Bar(BarType.from_str(bar_type_for(policy["symbol"], level)),
                                    *(Price.from_str(str(row[i])) for i in range(1, 5)),
                                    Quantity.from_str(str(row[5])), (right - 1) * 1000000, (right - 1) * 1000000))
                bars = missing_bars(catalog, bars)
                install_bars(catalog, bars)
                written += len(bars)
                next_ms = int(rows[-1][6]) + 1
                if next_ms <= cursor:
                    raise ValueError("历史分页没有推进")
                cursor = next_ms
        day = finish
    return {"ready": True, "records_written": written, "start": start.isoformat(), "end": end.isoformat(),
                "scope": "pair_history", "message": "历史追加完成；文件覆盖不代替逐笔质量校验。"}


def history_configuration(policy):
    return hashlib.sha256(json.dumps({key: policy.get(key) for key in
        ("product", "symbol", "base_level", "intervals", "history_mode", "history_start", "history_days")}, sort_keys=True).encode()).hexdigest()


async def serve_history(policy, settings, root, *, rest=None):
    """Continuous source backfill. Checkpoints and change delivery are file-owned."""
    root.mkdir(parents=True, exist_ok=True)
    rest = rest or PairREST(symbols=(policy["symbol"],))
    checkpoint = root / "checkpoint.json"
    digest = history_configuration(policy)
    saved = json.loads(checkpoint.read_text()) if checkpoint.exists() else {}
    if saved and saved.get("configuration") != digest:
        saved = {}
    source = "tick" if policy["base_level"] == "tick" else "1s" if policy["base_level"] == "1s" else "1m"
    # Internal minute inputs for larger minimums stay outside the public Catalog.
    source_settings = (replace(settings, catalog_dir=root / "source_catalog")
                       if policy["base_level"] not in {"tick", "1s", "1m", "auto"} and "1m" not in policy["intervals"] else settings)
    source_policy = {**policy, "base_level": source, "intervals": []}
    state = {"state": "WARMING_UP", "backlog": None, "last_error": None}

    async def heartbeat():
        while True:
            now = int(time.time() * 1000)
            write_record(root / "status.json", {**state, "checked_ms": now, "valid_until_ms": now + 15000})
            await asyncio.sleep(5)

    beat = asyncio.create_task(heartbeat())
    try:
        while True:
            try:
                if not saved:
                    catalog = ParquetDataCatalog(settings.catalog_dir)
                    start, _ = history_bounds(policy, catalog, datetime.now(UTC))
                    earliest = await rest.get("klines", symbol=policy["symbol"], interval="1m", startTime=0, limit=1)
                    if not earliest:
                        raise ValueError("交易所没有可用历史")
                    available = datetime.fromtimestamp(int(earliest[0][0]) / 1000, UTC).replace(hour=0, minute=0, second=0, microsecond=0)
                    start = max(start or available, available)
                    saved = {"configuration": digest, "start": start.isoformat(), "next_day": start.isoformat(),
                             "available_ms": int(earliest[0][0]), "verification_version": 1}
                    write_record(checkpoint, saved)
                if saved.get("verification_version") != 1:
                    earliest = await rest.get("klines", symbol=policy["symbol"], interval="1m", startTime=0, limit=1)
                    if not earliest:
                        raise ValueError("交易所没有可用历史")
                    saved.update(next_day=saved["start"], available_ms=int(earliest[0][0]), verification_version=1)
                    write_record(checkpoint, saved)
                day = datetime.fromisoformat(saved["next_day"])
                end = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
                unresolved = saved.get("unresolved_days", {})
                state.update(state="DEGRADED" if unresolved else "READY", backlog=max(0, (end - day).days),
                             unresolved_days=len(unresolved), last_error="历史存在未解决的对照差异" if unresolved else None)
                if day >= end:
                    if time.time() >= saved.get("next_scan_at", 0):
                        saved.update(next_day=saved["start"], next_scan_at=time.time() + 300)
                        write_record(checkpoint, saved)
                    await asyncio.sleep(30)
                    continue
                if saved.get("next_scan_at", 0) > time.time() and day.isoformat() == saved["start"]:
                    await asyncio.sleep(30)
                    continue
                finish = day + timedelta(days=1)
                change = root / "changes" / f"{day.date()}.json"
                event = {"state": "writing", "start": day.isoformat(), "end": finish.isoformat(),
                         "source": source, "source_catalog": str(source_settings.catalog_dir),
                         "policy": policy, "configuration": digest, "coverage_start": saved["start"]}
                with (root / "changes.lock").open("a") as lease:
                    fcntl.flock(lease, fcntl.LOCK_EX)
                    write_record(change, event)
                state.update(state="DEGRADED" if unresolved else "WARMING_UP", current_day=str(day.date()))
                lock_path = PROJECT_ROOT / ".local/run/api/data" / policy["product"] / f"{policy['symbol']}.job.lock"
                lock_path.parent.mkdir(parents=True, exist_ok=True)
                with lock_path.open("a") as lease:
                    try:
                        fcntl.flock(lease, fcntl.LOCK_SH | fcntl.LOCK_NB)
                    except BlockingIOError:
                        await asyncio.sleep(5)
                        continue
                    from pipeline.pair_repair import reconcile_day
                    result = await reconcile_day(source_policy, source_settings, rest, source, day, finish, saved["available_ms"])
                    write_record(root / "audits" / f"{day.date()}.json", result)
                    if result["ready"]:
                        unresolved.pop(str(day.date()), None)
                    else:
                        unresolved[str(day.date())] = result["checked_at"]
                    saved["unresolved_days"] = unresolved
                # Publish before advancing. A crash in either step safely replays this day.
                with (root / "changes.lock").open("a") as lease:
                    fcntl.flock(lease, fcntl.LOCK_EX)
                    write_record(change, {**event, "state": "ready"})
                    saved["next_day"] = finish.isoformat()
                    write_record(checkpoint, saved)
                state["last_processed_ms"] = int(finish.timestamp() * 1000)
            except (OSError, ValueError, RuntimeError, TimeoutError) as exc:
                state.update(state="DEGRADED", last_error=str(exc))
                await asyncio.sleep(30)
    finally:
        beat.cancel()
        await asyncio.gather(beat, return_exceptions=True)


def calculate_history_change(event, destination, *, completed_until):
    """Derive only complete affected windows, using bounded source streams."""
    import pyarrow as pa
    from nautilus_trader.model import NautilusDataType

    policy = event["policy"]
    source_level = event["source"]
    source = ParquetDataCatalog(event["source_catalog"])
    target = ParquetDataCatalog(destination)
    instrument_id = instrument_id_for(policy["symbol"])
    instruments = source.instruments(instrument_ids=[instrument_id])
    if not instruments:
        raise ValueError("历史计算缺少合约精度；保留待办")
    instrument = max(instruments, key=lambda item: item.ts_init)
    if not target.instruments(instrument_ids=[instrument_id]):
        target.write_data([instrument])
    price_step = Decimal(1).scaleb(-instrument.price_precision)
    size_step = Decimal(1).scaleb(-instrument.size_precision)
    start_ms = int(datetime.fromisoformat(event["start"]).timestamp() * 1000)
    end_ms = int(datetime.fromisoformat(event["end"]).timestamp() * 1000)
    lower_ms = int(datetime.fromisoformat(event.get("coverage_start", event["start"])).timestamp() * 1000)
    upper_ms = int(datetime.fromisoformat(completed_until).timestamp() * 1000)
    levels = list(dict.fromkeys(([policy["base_level"]] if policy["base_level"] not in {"auto", "tick"} else []) + policy["intervals"]))
    written = 0
    for level in levels:
        if level == source_level:
            continue
        left = bar_window(level, start_ms)[0]
        right = bar_window(level, end_ms - 1)[1]
        data_type = NautilusDataType.TradeTick if source_level == "tick" else NautilusDataType.Bar
        identifier = instrument_id_for(policy["symbol"]) if source_level == "tick" else bar_type_for(policy["symbol"], source_level)
        stream = source.native.query_data_arrow_stream(data_type, [identifier], left * 1000000, right * 1000000 - 1, display=False)
        reader = pa.RecordBatchReader._import_from_c_capsule(stream)
        buckets = {}
        source_ms = 1000 if source_level == "1s" else 60000
        try:
            for batch in reader:
                stamps = batch.column("ts_event").cast(pa.int64()).to_pylist()
                for index, row in enumerate(batch.to_pylist()):
                    stamp = stamps[index] // 1000000
                    opening_ms = stamp if source_level == "tick" else stamp + 1 - source_ms
                    a, b = bar_window(level, opening_ms)
                    if a < lower_ms or b > upper_ms:
                        continue
                    values = ([Decimal(str(row["price"]))] * 4 + [Decimal(str(row["size"]))]
                              if source_level == "tick" else [Decimal(str(row[k])) for k in ("open", "high", "low", "close", "volume")])
                    key = (stamps[index], int(row["trade_id"])) if source_level == "tick" else opening_ms
                    previous = buckets.get(a)
                    if previous is None:
                        buckets[a] = [b, *values, key, key, 1]
                    else:
                        previous[2] = max(previous[2], values[1])
                        previous[3] = min(previous[3], values[2])
                        previous[5] += values[4]
                        if source_level == "tick":
                            # Additive repair shards may interleave at the same millisecond.
                            if key < previous[6]:
                                previous[1], previous[6] = values[0], key
                            if key > previous[7]:
                                previous[4], previous[7] = values[3], key
                        else:
                            if opening_ms != previous[7] + source_ms:
                                raise ValueError("历史计算来源存在缺口或重复；待办已保留")
                            previous[4], previous[7] = values[3], key
                        previous[8] += 1
        finally:
            reader.close()
        bars = []
        for a, (b, opening, high, low, close, volume, first, last, count) in sorted(buckets.items()):
            if source_level != "tick" and (first != a or last + source_ms != b or count * source_ms != b - a):
                continue
            if any(value != value.quantize(price_step) for value in (opening, high, low, close)) or volume != volume.quantize(size_step):
                raise ValueError("历史来源精度与合约不一致；保留待办")
            bar = Bar(BarType.from_str(bar_type_for(policy["symbol"], level)),
                      *(Price.from_str(str(value.quantize(price_step))) for value in (opening, high, low, close)),
                      Quantity.from_str(str(volume.quantize(size_step))), (b - 1) * 1000000, (b - 1) * 1000000)
            bars.append(bar)
        # Keep existing authoritative files; a conflicting close is evidence, never silently replaced.
        if bars:
            existing = {bar.ts_event: bar for bar in target.bars(bar_types=[str(bars[0].bar_type)], start=bars[0].ts_event, end=bars[-1].ts_event)}
            for bar in bars:
                old = existing.get(bar.ts_event)
                if old is not None and any(getattr(old, key) != getattr(bar, key) for key in ("open", "high", "low", "close", "volume")):
                    raise ValueError("历史派生结果与已有闭合数据冲突；保留来源与待办")
            fresh = [bar for bar in bars if bar.ts_event not in existing]
            install_bars(target, fresh)
            written += len(fresh)
    return written


def main():
    parser = argparse.ArgumentParser(description="Explicit historical pair backfill")
    parser.add_argument("--request", required=True)
    parser.add_argument("--job-id")
    parser.add_argument("--service", action="store_true")
    parser.add_argument("--root", type=Path)
    args = parser.parse_args()
    import re
    if not args.service and not re.fullmatch(r"[0-9a-f]{32}", args.job_id or ""):
        parser.error("Invalid job id")
    from core.market.instruments import INTERVALS, normalize_symbol
    policy = json.loads(args.request)
    policy["symbol"] = normalize_symbol(policy["symbol"], policy["product"])
    if policy["base_level"] not in {"auto", "tick", *INTERVALS} or any(level not in INTERVALS for level in policy["intervals"]):
        parser.error("Invalid pair intervals")
    if args.service:
        if args.root is None or not (PROJECT_ROOT / "data").is_dir() or not args.root.resolve().is_relative_to((PROJECT_ROOT / "data").resolve()):
            parser.error("Invalid history root or unavailable mount")
        root = args.root / "history"
        root.mkdir(parents=True, exist_ok=True)
        with (root / "writer.lock").open("a") as lease:
            fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
            asyncio.run(serve_history(policy, load_settings(), root))
        return
    result = asyncio.run(backfill(policy, load_settings()))
    target = PROJECT_ROOT / ".local/state/api/data_jobs" / f"{args.job_id}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(result, ensure_ascii=False))
    os.replace(temporary, target)


if __name__ == "__main__":
    main()
