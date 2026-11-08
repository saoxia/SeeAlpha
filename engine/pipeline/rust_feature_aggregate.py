"""Arrow IPC bridge for the optional bounded-memory Rust feature aggregator.

Source trades are validated by Python. Record batches are typed and serialized
without CSV formatting; only aggregated buckets are materialized as a DataFrame.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa

BINARY = Path(__file__).resolve().parent / "rust_converter" / "target" / "release" / "feature-aggregate"
COLUMNS = ("open", "high", "low", "close", "volume", "quote_volume",
           "taker_buy_volume", "taker_buy_quote_volume", "trade_count")
OUTPUT_COLUMNS = ("bucket_ms", *COLUMNS)
INPUT_SCHEMA = pa.schema([
    pa.field("timestamp", pa.int64(), nullable=False),
    pa.field("price", pa.float64(), nullable=False),
    pa.field("quantity", pa.float64(), nullable=False),
    pa.field("quote_quantity", pa.float64(), nullable=False),
    pa.field("buyer_maker", pa.bool_(), nullable=False),
])
OUTPUT_SCHEMA = pa.schema([
    pa.field("bucket_ms", pa.int64(), nullable=False),
    *[pa.field(name, pa.float64(), nullable=False) for name in COLUMNS[:-1]],
    pa.field("trade_count", pa.int64(), nullable=False),
])


def feature_aggregator_binary() -> Path | None:
    configured = os.environ.get("SEEALPHA_RUST_FEATURE_AGGREGATOR")
    if configured is not None:
        if configured.strip().lower() in {"", "0", "false", "off"}:
            return None
        binary = Path(configured).expanduser()
    else:
        binary = BINARY
    return binary.resolve() if binary.is_file() and os.access(binary, os.X_OK) else None


def write_trade_chunk(writer: pa.ipc.RecordBatchStreamWriter, chunk: pd.DataFrame) -> None:
    """Emit one Arrow RecordBatch per validated chunk, preserving source row order."""
    arrays = [
        pa.array(chunk["timestamp"].to_numpy(dtype=np.int64, copy=False), type=pa.int64()),
        pa.array(chunk["price"].to_numpy(dtype=np.float64, copy=False), type=pa.float64()),
        pa.array(chunk["quantity"].to_numpy(dtype=np.float64, copy=False), type=pa.float64()),
        pa.array(chunk["quote_quantity"].to_numpy(dtype=np.float64, copy=False), type=pa.float64()),
        pa.array(chunk["buyer_maker"].to_numpy(dtype=np.bool_, copy=False), type=pa.bool_()),
    ]
    writer.write_batch(pa.RecordBatch.from_arrays(arrays, schema=INPUT_SCHEMA))


def run_aggregator(binary: Path, source, period_ms: int) -> pd.DataFrame:
    from engine.pipeline.convert_binance_bars import ConversionError

    source.flush()
    source.seek(0)
    with tempfile.TemporaryFile(mode="w+b") as output:
        completed = subprocess.run(
            [str(binary), str(period_ms)],
            stdin=source,
            stdout=output,
            stderr=subprocess.PIPE,
            check=False,
        )
        if completed.returncode:
            raise ConversionError(
                "Rust feature aggregation failed: "
                + (completed.stderr.decode("utf-8", errors="replace").strip()
                   or str(completed.returncode))
            )
        output.seek(0)
        try:
            reader = pa.ipc.open_stream(output)
            if not reader.schema.equals(OUTPUT_SCHEMA, check_metadata=False):
                raise ConversionError("Rust feature aggregation returned invalid Arrow schema")
            table = reader.read_all()
        except pa.ArrowException as exc:
            raise ConversionError("Rust feature aggregation returned invalid Arrow IPC") from exc
    if table.num_rows == 0:
        raise ConversionError("Rust feature aggregation returned no bars")
    frame = table.to_pandas()
    return frame.set_index("bucket_ms").rename_axis(None).loc[:, COLUMNS]
