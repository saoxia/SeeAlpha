"""Auditable raw minute OHLC and complete UTC aggregates for mechanism research."""

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parents[2]
END = pd.Timestamp("2026-08-31", tz="UTC")


def decode(column):
    words = np.frombuffer(b"".join(column.to_pylist()), dtype="<u8").reshape(-1, 2)
    return (words[:, 0].astype(float) + words[:, 1].astype(float) * 2.0**64) / 1e16


def load_minutes(asset):
    cache = ROOT / f"{asset}_minutes.parquet"
    if cache.exists():
        return pd.read_parquet(cache)
    directory = PROJECT / f"data/catalog/data/bar/{asset}USDT-PERP.BINANCE-1-MINUTE-LAST-EXTERNAL"
    frames, hashes = [], {}
    for path in sorted(directory.glob("*.parquet")):
        if path.name[:10] >= "2026-08-31":
            continue
        hashes[str(path.relative_to(PROJECT))] = hashlib.sha256(path.read_bytes()).hexdigest()
        table = pq.read_table(path, columns=["ts_event", "open", "high", "low", "close"])
        values = {name: decode(table[name]) for name in ("open", "high", "low", "close")}
        frames.append(
            pd.DataFrame(
                values, index=pd.to_datetime(table["ts_event"].to_numpy() + 1_000_000, utc=True)
            )
        )
    frame = pd.concat(frames).sort_index()
    frame = frame[frame.index <= END]
    assert not frame.index.has_duplicates
    assert np.all(frame.index.as_unit("ns").asi8 % (60 * 10**9) == 0)
    assert (frame.low > 0).all() and (frame.high >= frame.low).all()
    assert (frame.high >= frame[["open", "close"]].max(axis=1)).all()
    assert (frame.low <= frame[["open", "close"]].min(axis=1)).all()
    frame.to_parquet(cache)
    audit = {
        "asset": asset,
        "rows": len(frame),
        "first_end": str(frame.index[0]),
        "last_end": str(frame.index[-1]),
        "source_hashes": hashes,
        "cache_sha256": hashlib.sha256(cache.read_bytes()).hexdigest(),
    }
    (ROOT / f"{asset}_data_audit.json").write_text(json.dumps(audit, indent=2) + "\n")
    print("CACHED", asset, len(frame), flush=True)
    return frame


def aggregate(frame, minutes):
    grouped = frame.resample(f"{minutes}min", label="right", closed="right", origin="epoch")
    bars = grouped.agg({"open": "first", "high": "max", "low": "min", "close": "last"})
    return bars[grouped.close.count() == minutes]


if __name__ == "__main__":
    for asset in ("ETH", "BTC"):
        frame = load_minutes(asset)
        for minutes in (5, 15, 60, 1440):
            aggregate(frame, minutes).to_parquet(ROOT / f"{asset}_{minutes}m.parquet")
        print("AGGREGATED", asset, flush=True)
