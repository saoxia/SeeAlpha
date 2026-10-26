"""Temporal/data invariants for untruncated signal path research."""

import numpy as np
import pandas as pd
import pyarrow as pa

from strategy_python._indicator.bars import CompletedBarAggregator
from strategy_python._research.mechanism_20260915 import events
from strategy_python._research.mechanism_20260915.data import aggregate, decode
from strategy_python.btc_trend_following_short_5min.indicators import DailyBollEntryFilter


def prices(index):
    close = 100 + np.sin(np.arange(len(index)) / 20)
    return pd.DataFrame(
        {"open": close, "high": close + 0.1, "low": close - 0.1, "close": close}, index=index
    )


def test_fixed_point_decoder_including_values_above_uint64():
    expected = [123.45, 100000.12]
    column = pa.array(
        [round(value * 1e16).to_bytes(16, "little") for value in expected], type=pa.binary(16)
    )
    np.testing.assert_allclose(decode(column), expected, atol=1e-10)


def test_complete_aggregation_matches_production_across_gaps():
    frame = prices(pd.date_range("2021-01-01T00:01Z", periods=200, freq="min")).drop(
        pd.Timestamp("2021-01-01T01:03Z")
    )
    model = CompletedBarAggregator(5)
    actual = []
    for ts, row in frame.iterrows():
        bar = model.update(ts.value - 1_000_000, *row)
        if bar:
            actual.append([bar.ts_end, bar.open, bar.high, bar.low, bar.close])
    research = aggregate(frame, 5)
    np.testing.assert_array_equal(
        research.index.as_unit("ns").asi8, np.array(actual)[:, 0].astype("int64")
    )
    np.testing.assert_allclose(research.to_numpy(), np.array(actual)[:, 1:])


def test_daily_gate_at_complete_five_minute_boundaries_matches_minute_gate():
    frame = prices(pd.date_range("2021-01-01T00:01Z", periods=23 * 1440, freq="min"))
    frame = frame.drop(pd.Timestamp("2021-01-21T11:03Z"))
    minute, five = DailyBollEntryFilter(20, 2), DailyBollEntryFilter(20, 2, source_seconds=300)
    expected = {}
    for ts, row in frame.iterrows():
        minute.update(ts.value - 1_000_000, row.low, row.close)
        if ts.minute % 5 == 0:
            expected[ts.value] = minute.allowed
    for ts, row in aggregate(frame, 5).iterrows():
        five.update(ts.value - 1_000_000, row.low, row.close)
        assert five.allowed == expected[ts.value]


def test_path_labels_exclude_entry_bar_gaps_and_split_crossings(tmp_path, monkeypatch):
    index = pd.date_range("2023-12-29T00:01Z", periods=7 * 1440, freq="min")
    frame = prices(index)
    entry = pd.Timestamp("2023-12-29T01:00Z")
    # Entry minute must not leak into future MFE/MAE.
    frame.loc[entry, "high"] = 10000
    frame.loc[entry, "low"] = 0.01
    frame = frame.drop(pd.Timestamp("2023-12-30T03:00Z"))
    records = []
    for ts in [entry, pd.Timestamp("2023-12-31T23:00Z")]:
        records.append(
            {
                "asset": "TEST",
                "family": "TFL",
                "variant": "shared",
                "signal_ns": ts.value,
                "entry_ns": ts.value,
                "price": 100.0,
                "direction": 1,
                "eligible": True,
            }
        )
    pd.DataFrame(records).to_parquet(tmp_path / "TEST_events.parquet")
    monkeypatch.setattr(events, "ROOT", tmp_path)
    monkeypatch.setattr(events, "load_minutes", lambda _: frame)
    events.label("TEST")
    result = pd.read_parquet(tmp_path / "TEST_paths.parquet")
    assert set(result.hours) == {6, 24}
    assert set(result.entry_ns) == {entry.value}
    assert (result.mfe < 0.1).all() and (result.mae < 0.1).all()
    assert (result.label_end_ns < pd.Timestamp("2024-01-01", tz="UTC").value).all()
