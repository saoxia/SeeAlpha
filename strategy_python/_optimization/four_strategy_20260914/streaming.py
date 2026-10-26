"""Bounded-memory native final evaluations; same strategies and execution engine."""

import argparse
import fcntl
import importlib
import json
import time
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
from strategy_python._optimization.four_strategy_20260914.environment import backtest_spec
from strategy_python._optimization.four_strategy_20260914.research import (
    BASE,
    CANDIDATES,
    NAMES,
    PERIODS,
    measure,
)
from strategy_python.portfolio import STRATEGY_REGISTRY

ROOT = Path(__file__).resolve().parent


def run(key, period, candidate, optimized=False):
    start, end = [datetime.fromisoformat(t).replace(tzinfo=UTC) for t in PERIODS[period]]
    spec = backtest_spec(key)
    config_class, strategy_class = STRATEGY_REGISTRY[NAMES[key]]
    if optimized:
        from strategy_python._optimization.four_strategy_20260914.materialize import (
            CLASSES,
            PACKAGES,
        )

        module = importlib.import_module("strategy_python." + PACKAGES[key])
        config_class = getattr(module, CLASSES[key][1] + "Config")
        strategy_class = getattr(module, CLASSES[key][1] + "Strategy")
    output = (
        ROOT
        / (
            "stream_audit"
            if period in ("pilot", "parity")
            else "frozen_runs"
            if optimized
            else "runs"
        )
        / key
        / period
        / candidate
    )
    output.mkdir(parents=True, exist_ok=True)
    lockfile = (output / ".lock").open("w")
    fcntl.flock(lockfile, fcntl.LOCK_EX)
    if (output / "metrics.json").exists():
        lockfile.close()
        return
    instrument = max(
        ParquetDataCatalog(str(load_settings().catalog_dir)).instruments(
            instrument_ids=[str(spec.instrument_id)]
        ),
        key=lambda i: i.ts_init,
    )
    strategy = strategy_class(
        config_class(
            instrument_id=spec.instrument_id,
            bar_type=spec.bar_type,
            trade_start_ns=int(start.timestamp() * 1e9),
            **({} if optimized else {**BASE[key], **CANDIDATES[key][candidate]}),
        )
    )
    parameters = {
        "strategy": NAMES[key],
        "strategy_class": type(strategy).__module__ + "." + type(strategy).__name__,
        "candidate": candidate,
        "config": json.loads(strategy.config.json()),
        "start": start.isoformat(),
        "end": end.isoformat(),
        "maker_fee": str(instrument.maker_fee),
        "taker_fee": str(instrument.taker_fee),
        "nautilus_version": importlib.import_module("nautilus_trader").__version__,
        "streaming": True,
        "batch_days": 180,
    }
    (output / "parameters.json").write_text(json.dumps(parameters, indent=2) + "\n")
    engine = BacktestEngine(config=BacktestEngineConfig(logging=LoggingConfig(bypass_logging=True)))
    marks = []
    t = time.monotonic()
    try:
        engine.add_venue(
            venue=BINANCE_VENUE,
            oms_type=OmsType.HEDGING,
            account_type=AccountType.MARGIN,
            base_currency=None,
            starting_balances=[Money(500, instrument.quote_currency)],
            default_leverage=Decimal(5),
        )
        engine.add_instrument(instrument)
        engine.add_strategy(strategy)
        cursor = start - timedelta(days=spec.warmup_days)
        # Pilot deliberately crosses multiple batch boundaries to verify continuity.
        step = timedelta(days=7 if period in ("pilot", "parity") else 180)
        while cursor < end:
            batch_end = min(cursor + step, end)
            _, bars = load_binance_data(cursor, batch_end, spec, check_data=False)
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
            print("BATCH", key, period, candidate, str(batch_end), flush=True)
            cursor = batch_end
        engine.end()
        result = measure(engine, pd.concat(marks), start, end, output)
        result["elapsed_seconds"] = round(time.monotonic() - t, 2)
        (output / "metrics.json").write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")
        print("RESULT", key, period, candidate, json.dumps(result), flush=True)
    finally:
        engine.dispose()
        lockfile.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("strategy", choices=NAMES)
    p.add_argument("period", choices=PERIODS)
    p.add_argument("--candidate", default="baseline")
    p.add_argument("--optimized", action="store_true")
    args = p.parse_args()
    run(args.strategy, args.period, args.candidate, args.optimized)
