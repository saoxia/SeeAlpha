"""Arrow IPC bridge contract and parity tests for the optional Rust aggregator."""
from __future__ import annotations

import subprocess

import numpy as np
import pandas as pd
import pyarrow as pa
import pytest

from engine.pipeline.rust_feature_aggregate import (
    COLUMNS, INPUT_SCHEMA, OUTPUT_SCHEMA, feature_aggregator_binary,
    run_aggregator, write_trade_chunk,
)


def test_feature_aggregator_can_be_disabled(monkeypatch):
    monkeypatch.setenv("SEEALPHA_RUST_FEATURE_AGGREGATOR", "off")
    assert feature_aggregator_binary() is None


def write_input(path, batches):
    with path.open("w+b") as stream:
        with pa.ipc.new_stream(stream, INPUT_SCHEMA) as writer:
            for batch in batches:
                write_trade_chunk(writer, batch)


def test_ipc_schema_and_nullable_contract(tmp_path):
    frame = pd.DataFrame({
        "timestamp": [1, 2],
        "price": [100.0, 101.0],
        "quantity": [2.0, 3.0],
        "quote_quantity": [200.0, 303.0],
        "buyer_maker": [False, True],
    })
    path = tmp_path / "trades.arrow"
    write_input(path, [frame.iloc[:1], frame.iloc[1:]])
    with path.open("rb") as source:
        reader = pa.ipc.open_stream(source)
        assert reader.schema.equals(INPUT_SCHEMA)
        batches = list(reader)
    assert len(batches) == 2
    assert batches[0].schema.equals(INPUT_SCHEMA)
    assert batches[1].column(4).to_pylist() == [True]
    assert OUTPUT_SCHEMA.field("trade_count").type == pa.int64()


def test_rust_aggregates_open_high_low_close_and_exact_counts(tmp_path):
    binary = feature_aggregator_binary()
    if binary is None:
        pytest.skip("build cargo --release --bin feature-aggregate first")
    frame = pd.DataFrame({
        "timestamp": [60_000, 60_001, 61_000, 120_000],
        "price": [100.0, 110.0, 105.0, 95.0],
        "quantity": [1.0, 2.0, 3.0, 4.0],
        "quote_quantity": [100.0, 220.0, 315.0, 380.0],
        "buyer_maker": [True, False, False, True],
    })
    path = tmp_path / "trades.arrow"
    write_input(path, [frame.iloc[:2], frame.iloc[2:]])
    with path.open("rb") as stream:
        actual = run_aggregator(binary, stream, 60_000)
    assert list(actual.columns) == list(COLUMNS)
    assert actual.index.tolist() == [60_000, 120_000]
    first = actual.iloc[0]
    assert first["open"] == 100
    assert first["high"] == 110
    assert first["low"] == 100
    assert first["close"] == 105
    assert first["volume"] == pytest.approx(6)
    assert first["quote_volume"] == pytest.approx(635)
    assert first["taker_buy_volume"] == pytest.approx(5)
    assert first["taker_buy_quote_volume"] == pytest.approx(535)
    assert first["trade_count"] == 3
    assert actual.iloc[1]["trade_count"] == 1


def test_rust_rejects_invalid_trade_values(tmp_path):
    binary = feature_aggregator_binary()
    if binary is None:
        pytest.skip("build cargo --release --bin feature-aggregate first")
    path = tmp_path / "invalid.arrow"
    frame = pd.DataFrame({
        "timestamp": [60_000], "price": [-1.0], "quantity": [1.0],
        "quote_quantity": [1.0], "buyer_maker": [False],
    })
    write_input(path, [frame])
    completed = subprocess.run(
        [str(binary), "60000"], input=path.read_bytes(),
        capture_output=True, check=False,
    )
    assert completed.returncode != 0
    assert b"invalid trade" in completed.stderr


def test_rust_rejects_mismatched_schema(tmp_path):
    binary = feature_aggregator_binary()
    if binary is None:
        pytest.skip("build cargo --release --bin feature-aggregate first")
    path = tmp_path / "invalid-schema.arrow"
    with path.open("w+b") as stream:
        with pa.ipc.new_stream(stream, pa.schema([("price", pa.float64())])) as writer:
            writer.write_batch(pa.record_batch([[100.0]], names=["price"]))
    completed = subprocess.run(
        [str(binary), "60000"], input=path.read_bytes(),
        capture_output=True, check=False,
    )
    assert completed.returncode != 0
    assert b"invalid Arrow input schema" in completed.stderr


def test_rust_matches_python_bucket_aggregation_on_multiple_batches(tmp_path):
    binary = feature_aggregator_binary()
    if binary is None:
        pytest.skip("build cargo --release --bin feature-aggregate first")
    from engine.pipeline.bar_features import _aggregate
    values = pd.DataFrame({
        "timestamp": [1_000, 2_000, 60_000, 60_001, 120_000],
        "price": [1.25, 2.5, 3.0, 2.0, 4.0],
        "quantity": [1.0, 2.0, 3.0, 4.0, 5.0],
        "quote_quantity": [1.25, 5.0, 9.0, 8.0, 20.0],
        "buyer_maker": [False, True, True, False, False],
    })
    reference = pd.DataFrame({
        "open": values.price,
        "high": values.price,
        "low": values.price,
        "close": values.price,
        "volume": values.quantity,
        "quote_volume": values.quote_quantity,
        "taker_buy_volume": values.quantity.where(~values.buyer_maker, 0.0),
        "taker_buy_quote_volume": values.quote_quantity.where(~values.buyer_maker, 0.0),
        "trade_count": 1,
    })
    reference.index = (values.timestamp.to_numpy() // 60_000) * 60_000
    expected = _aggregate(reference)
    path = tmp_path / "input.arrow"
    write_input(path, [values.iloc[start:start + 2] for start in (0, 2, 4)])
    with path.open("rb") as source:
        actual = run_aggregator(binary, source, 60_000)
    pd.testing.assert_frame_equal(actual, expected, check_names=False,
                                  rtol=1e-12, atol=1e-12)
