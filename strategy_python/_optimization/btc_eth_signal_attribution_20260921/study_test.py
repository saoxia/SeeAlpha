"""Timing, gap, matching and gate tests independent of historical study outcomes."""

import numpy as np
import pandas as pd

from .analysis import (
    HOUR,
    MINUTE,
    block_interval,
    decision,
    match_controls,
    prepare_market,
    spaced,
    touches,
)


def market_fixture(n=6000):
    k = np.arange(n)
    close = 100 + 0.1 * np.sin(k / 13) + k / 10000
    return pd.DataFrame(
        {
            "ts": pd.Timestamp("2021-01-01", tz="UTC").value + (k + 1) * MINUTE - 1_000_000,
            "open": close,
            "high": close + 0.02,
            "low": close - 0.02,
            "close": close,
        }
    )


def test_quote_does_not_use_decision_bar_low_or_later_than_five_minutes():
    m = market_fixture(20)
    m.loc[:, ["open", "high", "low", "close"]] = [100.0, 101.0, 99.0, 100.0]
    m.loc[0, "low"] = 80
    m.loc[6, "low"] = 80
    q = pd.DataFrame({"ts": [m.ts.iloc[0]], "price": [90.0], "valid": [True], "eligible": [True]})
    assert touches(m, q).empty
    m.loc[5, "low"] = 89
    result = touches(m, q)
    assert result.i.tolist() == [5] and result.ts.iloc[0] == m.ts.iloc[5]


def test_marketable_quote_uses_close_and_gap_invalidates_touch():
    m = market_fixture(20)
    q = pd.DataFrame({"ts": [m.ts.iloc[0]], "price": [101.0], "valid": [True], "eligible": [True]})
    assert touches(m, q).i.tolist() == [0]
    q["price"] = 90.0
    m.loc[2, "low"] = 80
    m = m.drop(index=1).reset_index(drop=True)
    assert touches(m, q).empty


def test_forward_extrema_exclude_event_bar_and_cover_exact_horizon():
    m = market_fixture()
    m.loc[1700, "high"] = 1000
    p = prepare_market(m)
    i = 1700
    for h in (1, 4, 24):
        assert p.loc[i, f"high_{h}"] == m.high.iloc[i + 1 : i + h * 60 + 1].max()
        assert p.loc[i, f"low_{h}"] == m.low.iloc[i + 1 : i + h * 60 + 1].min()
        assert p.loc[i, f"close_{h}"] == m.close.iloc[i + h * 60]
    missing = prepare_market(m.drop(index=1750).reset_index(drop=True))
    assert not missing.complete.iloc[i] and pd.isna(missing.close_1.iloc[i])


def test_future_prices_do_not_change_past_vol_or_quote_detection():
    m = market_fixture()
    changed = m.copy()
    changed.loc[3001:, ["open", "high", "low", "close"]] *= 2
    np.testing.assert_allclose(
        prepare_market(m).vol.iloc[:3001], prepare_market(changed).vol.iloc[:3001], equal_nan=True
    )
    q = pd.DataFrame(
        {"ts": [m.ts.iloc[2990]], "price": [101.0], "valid": [True], "eligible": [True]}
    )
    pd.testing.assert_frame_equal(touches(m, q), touches(changed, q))


def test_matched_controls_respect_calendar_past_vol_and_distance():
    m = prepare_market(market_fixture(16000))
    pool = m.index[m.complete & m.vol.gt(0)].to_numpy()
    events = pd.DataFrame({"i": [7000, 8000, 9000]})
    ei, ci = match_controls(m, events, pool, 4)
    assert len(ei) == 3
    for i, controls in zip(ei, ci, strict=True):
        assert (m.month.iloc[controls] == m.month.iloc[i]).all()
        assert (m.hour_group.iloc[controls] == m.hour_group.iloc[i]).all()
        assert (np.abs(np.log(m.vol.iloc[controls] / m.vol.iloc[i])) <= 0.25 + 1e-12).all()
        assert (np.abs(m.ts.iloc[controls].to_numpy() - m.ts.iloc[i]) > 24 * HOUR).all()
    changed = m.copy()
    changed[["close_1", "close_4", "close_24"]] = -999
    ei2, ci2 = match_controls(changed, events, pool, 4)
    np.testing.assert_array_equal(ei, ei2)
    np.testing.assert_array_equal(ci, ci2)


def test_spacing_is_chronological_and_bootstrap_constant_difference():
    events = pd.DataFrame({"ts": [0, HOUR // 2, HOUR, 2 * HOUR, 24 * HOUR]})
    assert spaced(events, 1).ts.tolist() == [0, HOUR, 2 * HOUR, 24 * HOUR]
    assert spaced(events, 24).ts.tolist() == [0, 24 * HOUR]
    timestamps = pd.date_range("2021-01-01", periods=24, freq="MS", tz="UTC").as_unit("ns").asi8
    interval, months = block_interval(timestamps, np.ones(24) * 7, 2021 * 12, 2022 * 12 + 11)
    assert interval == [7.0, 7.0] and months == 24


def test_gate_requires_both_markets_periods_spacings_and_two_horizons():
    rows = []
    for kind, n in (("MRL", 16), ("TFS", 8)):
        for h in (1, 4, 24):
            rows.extend(
                {
                    "strategy": kind,
                    "horizon_hours": h,
                    "sufficient_count": True,
                    "edge_low95_bp": 1.0,
                }
                for _ in range(n)
            )
    frame = pd.DataFrame(rows)
    assert decision(frame)["MRL"]["signal_evidence_gate_passed"]
    frame.loc[(frame.strategy == "MRL") & (frame.horizon_hours != 1), "edge_low95_bp"] = -1
    assert not decision(frame)["MRL"]["signal_evidence_gate_passed"]
    assert decision(frame)["TFS"]["signal_evidence_gate_passed"]
