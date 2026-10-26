"""Four frozen native replays of the two current BTC long strategies."""

import argparse
import hashlib
import importlib
import importlib.metadata
import json
import os
import subprocess
import sys
import time
import zipfile
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pandas as pd
from nautilus_trader.adapters.binance import BINANCE_VENUE
from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.config import BacktestEngineConfig, LoggingConfig
from nautilus_trader.model.enums import AccountType, OmsType
from nautilus_trader.model.objects import Money
from nautilus_trader.persistence.catalog import ParquetDataCatalog

from pipeline.config import load_settings
from strategy_python._backtesting import load_binance_data
from strategy_python._execution.entry_constraints import opening_order_allowed
from strategy_python._optimization.tfs_stability_20260921.study import fee_instrument, measure
from strategy_python.btc import STRATEGIES, create_strategy, spec_for

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parents[2]
KEYS = {"trend_following_long": "TFL_original", "mean_reversion_long": "MRL_original"}
HISTORY = ROOT.parent / "btc_cycle_20260915/final_runs/full"


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def json_write(path, value):
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n")
    temp.replace(path)


def source_files():
    packages = [STRATEGIES[key]["package"] for key in KEYS]
    packages += ["_indicator", "_mechanism", "_risk", "_model", "_execution"]
    files = {p for name in packages for p in (PROJECT / "strategy_python" / name).rglob("*.py")}
    files.update((PROJECT / "pipeline").glob("*.py"))
    files.update(
        [
            PROJECT / "strategy_python/_backtesting.py",
            PROJECT / "strategy_python/btc.py",
            ROOT.parent / "tfs_stability_20260921/study.py",
            Path(__file__),
            ROOT / "PROTOCOL.md",
        ]
    )
    return sorted(files)


def data_files():
    return sorted(
        p
        for p in Path(load_settings().catalog_dir).rglob("*.parquet")
        if "BTCUSDT-PERP.BINANCE-1-MINUTE-LAST-EXTERNAL" in str(p)
        or ("crypto_perpetual" in str(p) and "BTCUSDT" in str(p))
    )


def environment():
    native = {}
    for name in ("nautilus_trader.backtest.engine", "nautilus_trader.risk.engine"):
        path = Path(importlib.import_module(name).__file__)
        native[name] = digest(path)
    return {
        "python": sys.version,
        "packages": {
            name: importlib.metadata.version(name)
            for name in ("nautilus_trader", "numpy", "pandas", "pyarrow")
        },
        "native_sha256": native,
    }


def freeze():
    if (ROOT / "manifest.json").exists():
        raise RuntimeError("Frozen manifest already exists")
    start = datetime(2021, 1, 1, tzinfo=UTC)
    comparisons = {}
    instrument = max(
        ParquetDataCatalog(str(load_settings().catalog_dir)).instruments(
            instrument_ids=[str(spec_for("trend_following_long").instrument_id)]
        ),
        key=lambda x: x.ts_init,
    )
    for key, slot in KEYS.items():
        old = json.loads((HISTORY / slot / "parameters.json").read_text())
        current = json.loads(create_strategy(key, start).config.json())
        assert current == old["config"], (key, "configuration drift")
        assert spec_for(key).warmup_days == old["warmup_days"]
        assert old["starting_balance"] == 500 and old["account_leverage"] == 5
        assert old["start"] == start.isoformat()
        assert old["end"] == datetime(2026, 8, 31, tzinfo=UTC).isoformat()
        for field in ("maker_fee", "taker_fee", "price_increment", "size_increment"):
            assert Decimal(str(getattr(instrument, field))) == Decimal(old[field]), field
        assert str(instrument.min_notional) == old["min_notional"]
        assert importlib.metadata.version("nautilus_trader") == old["nautilus_version"]
        comparisons[key] = {
            "historical_slot": slot,
            "config_identical": True,
            "warmup_days": spec_for(key).warmup_days,
            "fee_and_instrument_bounds_identical": True,
        }
    sources = source_files()
    data = data_files()
    assert sources and data
    references = [
        HISTORY / slot / name
        for slot in KEYS.values()
        for name in ("metrics.json", "parameters.json", "fills.csv", "daily_equity.csv")
    ]
    manifest = {
        "created_at": datetime.now(UTC).isoformat(),
        "start": "2021-01-01",
        "end_exclusive": "2026-08-31",
        "experiments": [
            {"id": key + f"_fee{fee}", "strategy": key, "extra_fee_bp": fee}
            for key in KEYS
            for fee in (0, 5)
        ],
        "historical_config_comparison": comparisons,
        "source_sha256": {str(p.relative_to(PROJECT)): digest(p) for p in sources},
        "data_sha256": {str(p.relative_to(PROJECT)): digest(p) for p in data},
        "reference_sha256": {str(p.relative_to(PROJECT)): digest(p) for p in references},
        "environment": environment(),
    }
    json_write(ROOT / "manifest.json", manifest)
    with zipfile.ZipFile(ROOT / "frozen_sources.zip", "w", compression=zipfile.ZIP_DEFLATED) as z:
        for p in sources + [ROOT / "manifest.json"]:
            z.write(p, str(p.relative_to(PROJECT)))
    with zipfile.ZipFile(ROOT / "frozen_sources.zip") as z:
        assert z.testzip() is None
        for name, expected in manifest["source_sha256"].items():
            assert hashlib.sha256(z.read(name)).hexdigest() == expected
    json_write(ROOT / "archive.json", {"sha256": digest(ROOT / "frozen_sources.zip")})
    print("FROZEN", len(sources), "source inputs", len(data), "data files", flush=True)


def verify(manifest):
    for label, files in (("source", source_files()), ("data", data_files())):
        actual = {str(p.relative_to(PROJECT)) for p in files}
        if actual != set(manifest[label + "_sha256"]):
            raise RuntimeError(f"Frozen {label} file inventory changed")
    for label in ("source", "data", "reference"):
        for name, expected in manifest[label + "_sha256"].items():
            if digest(PROJECT / name) != expected:
                raise RuntimeError(f"Frozen input changed: {name}")
    if environment() != manifest["environment"]:
        raise RuntimeError("Runtime environment changed")


def execute(experiment_id):
    manifest = json.loads((ROOT / "manifest.json").read_text())
    verify(manifest)
    row = next(r for r in manifest["experiments"] if r["id"] == experiment_id)
    start = datetime.fromisoformat(manifest["start"]).replace(tzinfo=UTC)
    end = datetime.fromisoformat(manifest["end_exclusive"]).replace(tzinfo=UTC)
    spec = spec_for(row["strategy"])
    strategy = create_strategy(row["strategy"], start)
    catalog = ParquetDataCatalog(str(load_settings().catalog_dir))
    original = max(
        catalog.instruments(instrument_ids=[str(spec.instrument_id)]), key=lambda x: x.ts_init
    )
    instrument = fee_instrument(original, row["extra_fee_bp"])
    params = {
        "experiment": row,
        "manifest_sha256": digest(ROOT / "manifest.json"),
        "config": json.loads(strategy.config.json()),
        "instrument": instrument.to_dict(instrument),
        "start": start.isoformat(),
        "end_exclusive": end.isoformat(),
        "warmup_days": spec.warmup_days,
        "starting_balance": 500,
        "leverage": 5,
        "batch_days": 90,
    }
    output = ROOT / "runs" / experiment_id
    output.mkdir(parents=True, exist_ok=True)
    if (output / "completion.json").exists():
        assert json.loads((output / "parameters.json").read_text()) == params
        for name, expected in json.loads((output / "completion.json").read_text()).items():
            assert digest(output / name) == expected, name
        print("REUSED", experiment_id, flush=True)
        return
    json_write(output / "parameters.json", params)
    engine = BacktestEngine(config=BacktestEngineConfig(logging=LoggingConfig(bypass_logging=True)))
    marks = []
    began = time.monotonic()
    try:
        engine.add_venue(
            venue=BINANCE_VENUE,
            oms_type=OmsType.HEDGING,
            account_type=AccountType.MARGIN,
            base_currency=None,
            starting_balances=[Money(500, instrument.quote_currency)],
            default_leverage=spec.leverage,
        )
        engine.add_instrument(instrument)
        engine.add_strategy(strategy)
        cursor = start - timedelta(days=spec.warmup_days)
        while cursor < end:
            stop = min(cursor + timedelta(days=90), end)
            _, bars = load_binance_data(cursor, stop, spec, check_data=False)
            if bars:
                marks.append(
                    pd.Series(
                        [float(b.close) for b in bars],
                        index=pd.to_datetime([b.ts_event for b in bars], utc=True),
                    )
                )
                engine.add_data(bars)
                engine.run(streaming=True)
                engine.clear_data()
            cursor = stop
            print("BATCH", experiment_id, stop.isoformat(), flush=True)
        engine.end()
        result = measure(engine, pd.concat(marks), start, end, output, 500)
        fills = engine.trader.generate_order_fills_report()
        entries = fills[~fills.is_reduce_only.astype(bool)] if not fills.empty else fills
        invalid = sum(
            not opening_order_allowed(
                instrument, Decimal(str(f.filled_qty)), Decimal(str(f.avg_px))
            )
            for f in entries.itertuples()
        )
        off_tick, reasons, statuses = 0, Counter(), Counter()
        for order in engine.cache.orders():
            statuses[str(order.status)] += 1
            for field in ("price", "trigger_price"):
                value = getattr(order, field, None)
                if (
                    value is not None
                    and value.as_decimal() % instrument.price_increment.as_decimal() != 0
                ):
                    off_tick += 1
            for event in order.events:
                if reason := getattr(event, "reason", None):
                    reasons[type(event).__name__ + ": " + str(reason)] += 1
        if invalid or off_tick or engine.cache.positions_open():
            raise RuntimeError(f"Execution audit failed: invalid={invalid}, off_tick={off_tick}")
        result.update(
            entry_fills_outside_constraints=invalid,
            off_tick_order_price_fields=off_tick,
            order_event_reasons=dict(reasons),
            order_status_counts=dict(statuses),
            first_fill=str(fills.ts_last.min()) if len(fills) else None,
            last_fill=str(fills.ts_last.max()) if len(fills) else None,
            entry_fills=len(entries),
            elapsed_seconds=round(time.monotonic() - began, 2),
        )
        verify(manifest)
        json_write(output / "metrics.json", result)
        json_write(
            output / "completion.json",
            {
                p.name: digest(p)
                for p in sorted(output.iterdir())
                if p.suffix in (".csv", ".json") and p.name != "completion.json"
            },
        )
        print("DONE", experiment_id, result["net_pnl"], result["trades"], flush=True)
    finally:
        engine.dispose()


def run_all():
    manifest = json.loads((ROOT / "manifest.json").read_text())
    verify(manifest)
    (ROOT / "logs").mkdir(exist_ok=True)

    def child(row):
        env = dict(os.environ, OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1", MKL_NUM_THREADS="1")
        with (ROOT / "logs" / (row["id"] + ".log")).open("w") as log:
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "strategy_python._optimization.btc_long_validation_20260921.run",
                    "run",
                    row["id"],
                ],
                stdout=log,
                stderr=subprocess.STDOUT,
                env=env,
            )
        if result.returncode:
            raise RuntimeError(row["id"] + " failed; see log")
        return row["id"]

    completed, failed = [], []
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(child, row): row["id"] for row in manifest["experiments"]}
        for future in as_completed(futures):
            try:
                completed.append(future.result())
                print("COMPLETE", len(completed), "/ 4", completed[-1], flush=True)
            except RuntimeError as exc:
                failed.append(str(exc))
                print("FAILED", str(exc), flush=True)
            json_write(
                ROOT / "progress.json", {"completed": completed, "failed": failed, "total": 4}
            )
    verify(manifest)
    if failed:
        raise RuntimeError(str(failed))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("freeze", "verify", "run", "all"))
    parser.add_argument("experiment_id", nargs="?")
    args = parser.parse_args()
    if args.action == "freeze":
        freeze()
    elif args.action == "verify":
        verify(json.loads((ROOT / "manifest.json").read_text()))
    elif args.action == "run":
        execute(args.experiment_id)
    else:
        run_all()
