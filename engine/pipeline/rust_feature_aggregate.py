"""Stream validated trade batches to the optional Rust feature aggregator.

Python owns source validation and public DataFrame contracts; Rust only folds base
time buckets. The scratch files cap Python heap use independent of trade count.
"""
from __future__ import annotations

import csv
import os
import subprocess
import tempfile
from pathlib import Path

import pandas as pd

BINARY = Path(__file__).resolve().parent / "rust_converter" / "target" / "release" / "feature-aggregate"
COLUMNS = ("open", "high", "low", "close", "volume", "quote_volume",
           "taker_buy_volume", "taker_buy_quote_volume", "trade_count")
OUTPUT_COLUMNS = ("bucket_ms", *COLUMNS)


def feature_aggregator_binary() -> Path | None:
    configured = os.environ.get("SEEALPHA_RUST_FEATURE_AGGREGATOR")
    if configured is not None:
        if configured.strip().lower() in {"", "0", "false", "off"}:
            return None
        binary = Path(configured).expanduser()
    else:
        binary = BINARY
    return binary.resolve() if binary.is_file() and os.access(binary, os.X_OK) else None


def write_trade_chunk(writer: csv.writer, chunk: pd.DataFrame) -> None:
    """Write only primitive columns (not pandas rows or boxed Python trade objects)."""
    writer.writerows(zip(
        chunk["timestamp"].to_numpy(),
        chunk["price"].to_numpy(),
        chunk["quantity"].to_numpy(),
        chunk["quote_quantity"].to_numpy(),
        chunk["buyer_maker"].to_numpy(),
    ))


def run_aggregator(binary: Path, source, period_ms: int) -> pd.DataFrame:
    from engine.pipeline.convert_binance_bars import ConversionError

    source.flush()
    source.seek(0)
    with tempfile.TemporaryFile(mode="w+t", encoding="utf-8", newline="") as output:
        completed = subprocess.run(
            [str(binary), str(period_ms)],
            stdin=source,
            stdout=output,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        if completed.returncode:
            raise ConversionError(
                "Rust feature aggregation failed: "
                + (completed.stderr.strip() or str(completed.returncode))
            )
        output.seek(0)
        frame = pd.read_csv(output, header=None, names=OUTPUT_COLUMNS,
                            dtype={"bucket_ms": "int64", "trade_count": "int64"})
    if frame.empty:
        raise ConversionError("Rust feature aggregation returned no bars")
    return frame.set_index("bucket_ms").rename_axis(None).loc[:, COLUMNS]
