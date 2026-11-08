"""Rust feature aggregation contracts; requires a built release binary for integration."""
from __future__ import annotations

import csv
import os
import subprocess

import numpy as np
import pandas as pd
import pytest

from engine.pipeline.rust_feature_aggregate import (
    COLUMNS, feature_aggregator_binary, run_aggregator, write_trade_chunk,
)


def test_feature_aggregator_can_be_disabled(monkeypatch):
    monkeypatch.setenv("SEEALPHA_RUST_FEATURE_AGGREGATOR", "off")
    assert feature_aggregator_binary() is None


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
    path = tmp_path / "trades.csv"
    with path.open("w+", newline="") as stream:
        write_trade_chunk(csv.writer(stream), frame.iloc[:2])
        write_trade_chunk(csv.writer(stream), frame.iloc[2:])
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


def test_rust_rejects_invalid_trade_values():
    binary = feature_aggregator_binary()
    if binary is None:
        pytest.skip("build cargo --release --bin feature-aggregate first")
    completed = subprocess.run(
        [str(binary), "60000"],
        input="60000,-1,1,1,false\n",
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode != 0
    assert "invalid trade" in completed.stderr


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
    with (tmp_path / "input.csv").open("w+", newline="") as stream:
        writer = csv.writer(stream)
        for start in (0, 2, 4):
            write_trade_chunk(writer, values.iloc[start:start + 2])
        actual = run_aggregator(binary, stream, 60_000)
    pd.testing.assert_frame_equal(actual, expected, check_names=False,
                                  rtol=1e-12, atol=1e-12)
