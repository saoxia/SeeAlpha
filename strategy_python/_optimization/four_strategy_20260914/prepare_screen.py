"""Build isolated, complete 5m research bars from original local 1m catalog."""

from pathlib import Path

import pandas as pd

from strategy_python._backtesting import load_binance_data
from strategy_python._optimization.four_strategy_20260914.environment import backtest_spec

BACKTEST_SPEC = backtest_spec("MRL")

ROOT = Path(__file__).resolve().parent
parts = []
for start, end in zip(
    pd.date_range("2020-08-01", "2026-09-01", freq="MS", tz="UTC")[:-1],
    pd.date_range("2020-08-01", "2026-09-01", freq="MS", tz="UTC")[1:],
):
    instrument, bars = load_binance_data(
        start.to_pydatetime(), end.to_pydatetime(), BACKTEST_SPEC, check_data=False
    )
    if not bars:
        continue
    frame = pd.DataFrame(
        [
            (
                b.ts_event,
                float(b.open),
                float(b.high),
                float(b.low),
                float(b.close),
                float(b.volume),
            )
            for b in bars
        ],
        columns=["ts", "open", "high", "low", "close", "volume"],
    )
    if frame.ts.duplicated().any():
        raise RuntimeError("Duplicate bars")
    frame["bucket"] = frame.ts // 300_000_000_000
    grouped = frame.groupby("bucket").agg(
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        volume=("volume", "sum"),
        count=("ts", "count"),
        first=("ts", "min"),
        last=("ts", "max"),
    )
    grouped = grouped[
        (grouped["count"] == 5) & (grouped["last"] - grouped["first"] == 240_000_000_000)
    ]
    grouped["ts"] = (grouped.index + 1) * 300_000_000_000 - 1_000_000
    parts.append(grouped[["ts", "open", "high", "low", "close", "volume"]])
    print(start, len(bars), len(grouped), flush=True)
result = pd.concat(parts, ignore_index=True)
result.to_parquet(ROOT / "derived_5m.parquet", index=False)
print("COMPLETE", len(result), flush=True)
