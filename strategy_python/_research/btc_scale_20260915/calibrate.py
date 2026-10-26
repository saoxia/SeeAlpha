"""Freeze a price-return scale using only paired, complete UTC days in 2020.

No strategy returns or post-calibration market observations enter this estimator.
The median daily RV ratio is a modeling choice, not recovered parameter provenance.
"""

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parents[2]
MINUTE = 60_000_000_000
DAY = 1440 * MINUTE


def load_asset(symbol):
    directory = PROJECT / f"data/catalog/data/bar/{symbol}USDT-PERP.BINANCE-1-MINUTE-LAST-EXTERNAL"
    pieces, hashes = [], {}
    for path in sorted(directory.glob("*.parquet")):
        # Catalog files are daily partitions; select before loading any price data.
        if not path.name.startswith("2020-"):
            continue
        hashes[str(path.relative_to(PROJECT))] = hashlib.sha256(path.read_bytes()).hexdigest()
        table = pq.read_table(path, columns=["ts_event", "close"])
        # Nautilus fixed-point raw prices: their common multiplier cancels in ratios.
        prices = [
            float(int.from_bytes(x, "little", signed=True)) for x in table["close"].to_pylist()
        ]
        pieces.append(pd.Series(prices, index=table["ts_event"].to_numpy()))
    series = pd.concat(pieces).sort_index()
    start = pd.Timestamp("2020-01-01", tz="UTC").value
    end = pd.Timestamp("2021-01-01", tz="UTC").value
    series = series[(series.index >= start) & (series.index < end)]
    if series.index.has_duplicates or (series <= 0).any():
        raise ValueError("Duplicate timestamps or nonpositive prices")
    return series, hashes


def main():
    btc, bh = load_asset("BTC")
    eth, eh = load_asset("ETH")
    paired = pd.concat([btc.rename("BTC"), eth.rename("ETH")], axis=1).dropna()
    records = []
    for day, frame in paired.groupby(paired.index // DAY):
        if len(frame) != 1440 or not np.all(np.diff(frame.index) == MINUTE):
            continue
        returns = np.diff(np.log(frame.to_numpy()), axis=0)
        rv = np.sqrt(np.sum(returns * returns, axis=0))
        if np.any(rv <= 0):
            continue
        records.append(
            {
                "date": pd.Timestamp(int(day * DAY), tz="UTC").date().isoformat(),
                "btc_rv": rv[0],
                "eth_rv": rv[1],
                "ratio": rv[0] / rv[1],
            }
        )
    daily = pd.DataFrame(records)
    if len(daily) < 300:
        raise ValueError(
            "Need at least 300 complete paired days; do not silently change calibration"
        )
    factor = float(daily.ratio.median())
    if not 0 < factor < 2:
        raise ValueError("Unexpected scale; inspect data before building")
    daily.to_csv(ROOT / "daily_volatility.csv", index=False)
    result = {
        "calibration_start": "2020-01-01T00:00:00Z",
        "calibration_end_exclusive": "2021-01-01T00:00:00Z",
        "first_allowed_trade": "2021-01-01T00:00:00Z",
        "complete_paired_days": len(daily),
        "excluded_days": 366 - len(daily),
        "returns_per_day": 1439,
        "estimator": "median_day(sqrt(sum(BTC minute log returns^2))/sqrt(sum(ETH minute log returns^2)))",
        "factor": factor,
        "ratio_q25": float(daily.ratio.quantile(0.25)),
        "ratio_q75": float(daily.ratio.quantile(0.75)),
        "input_sha256": bh | eh,
    }
    (ROOT / "calibration.json").write_text(json.dumps(result, indent=2) + "\n")
    print({k: v for k, v in result.items() if k != "input_sha256"})


if __name__ == "__main__":
    main()
