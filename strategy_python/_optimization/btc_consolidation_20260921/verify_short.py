"""Continuous native historical replay of the newly frozen BTC short migration."""

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd
from nautilus_trader.adapters.binance import BINANCE_VENUE
from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.config import BacktestEngineConfig, LoggingConfig
from nautilus_trader.model.enums import AccountType, OmsType
from nautilus_trader.model.objects import Money
from nautilus_trader.persistence.catalog import ParquetDataCatalog

from engine.pipeline.config import load_settings
from strategy_python._backtesting import load_binance_data
from strategy_python._optimization.tfs_stability_20260921.study import measure
from strategy_python.btc import create_strategy, spec_for

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parents[2]


def main():
    start, end = datetime(2021, 1, 1, tzinfo=UTC), datetime(2026, 8, 31, tzinfo=UTC)
    output = ROOT / "mean_reversion_short_full"
    output.mkdir(exist_ok=True)
    spec = spec_for("mean_reversion_short")
    strategy = create_strategy("mean_reversion_short", start)
    instrument = max(
        ParquetDataCatalog(str(load_settings().catalog_dir)).instruments(
            instrument_ids=[str(spec.instrument_id)]
        ),
        key=lambda x: x.ts_init,
    )
    files = list((PROJECT / "strategy_python/btc_mean_reversion_short_5min").glob("*.py"))
    files += [
        Path(__file__),
        PROJECT / "strategy_python/btc.py",
        PROJECT / "strategy_python/_optimization/tfs_stability_20260921/study.py",
    ]
    hashes = {
        str(p.relative_to(PROJECT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files
    }
    params = {
        "start": start.isoformat(),
        "end_exclusive": end.isoformat(),
        "config": json.loads(strategy.config.json()),
        "source_sha256": hashes,
        "instrument": instrument.to_dict(instrument),
        "starting_balance": 500,
        "purpose": "frozen transfer historical diagnostic; no return-based parameter selection",
    }
    if (output / "metrics.json").exists():
        if json.loads((output / "parameters.json").read_text()) != params:
            raise RuntimeError("Completed replay inputs changed")
        return
    (output / "parameters.json").write_text(json.dumps(params, indent=2) + "\n")
    engine = BacktestEngine(config=BacktestEngineConfig(logging=LoggingConfig(bypass_logging=True)))
    marks = []
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
            print("BATCH", stop.isoformat(), flush=True)
        engine.end()
        result = measure(engine, pd.concat(marks), start, end, output, 500)
        for name, h in hashes.items():
            if hashlib.sha256((PROJECT / name).read_bytes()).hexdigest() != h:
                raise RuntimeError("Input changed during migration verification")
        (output / "metrics.json").write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")
        print("DONE", result["net_pnl"], result["trades"], flush=True)
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
