from dataclasses import replace

import pytest

from strategy_python._indicator.bars import CompletedBar
from strategy_python.mean_reversion_short_v2_5min.indicators import (
    Features,
    MeanReversionIndicators,
)
from strategy_python.mean_reversion_short_v2_5min.signal import mean_reversion_setup


def test_rejection_requires_mean_gap_and_hourly_down_background():
    f = Features(102, 104, 101, 100, 1, 1, 100, 103, False, True)
    opts = {
        "entry_kind": "wick",
        "regime": "down",
        "boll_std": 3.0,
        "min_mean_distance": 0.003,
        "weekend_only": True,
        "weekday": 5,
    }
    assert mean_reversion_setup(f, **opts)
    for changed in (
        replace(f, high=102.9),
        replace(f, open=100),
        replace(f, close=100.2),
        replace(f, down=False),
    ):
        assert not mean_reversion_setup(changed, **opts)
    for weekday in range(5):
        assert not mean_reversion_setup(f, **{**opts, "weekday": weekday})
    assert mean_reversion_setup(f, **{**opts, "weekday": 6})


def test_gap_requires_new_five_minute_window_and_stale_hour_expires():
    model = MeanReversionIndicators()
    step = 300_000_000_000
    for i in range(1, 22):
        bar = CompletedBar((i - 1) * step, i * step, 100, 102, 98, 101)
        f = model.update_five(bar)
    assert f is not None and not f.down
    assert model.update_five(CompletedBar(22 * step, 23 * step, 100, 102, 98, 101)) is None
    # Hourly state only remains valid inside the next clock hour.
    for i in range(1, 26):
        end = i * 3600_000_000_000
        close = 200 - i
        model.update_hour(
            CompletedBar(end - 3600_000_000_000, end, close, close + 1, close - 1, close)
        )
    assert model.down
    model.last_five = None
    model.closes.clear()
    model.ranges.clear()
    model.previous = None
    for i in range(1, 22):
        end = 25 * 3600_000_000_000 + i * step
        f = model.update_five(CompletedBar(end - step, end, 100, 102, 98, 101))
    assert f is not None and not f.down


def test_streaming_boll_atr_matches_independent_batch_calculation():
    import numpy as np
    import pandas as pd

    model = MeanReversionIndicators()
    x = np.arange(80)
    close = 100 + np.sin(x / 3) + x * 0.03
    tr = []
    for i, p in enumerate(close):
        h = p + 0.4
        l = p - 0.3
        if i:
            tr.append(max(h - l, abs(h - close[i - 1]), abs(l - close[i - 1])))
        f = model.update_five(
            CompletedBar(i * 300_000_000_000, (i + 1) * 300_000_000_000, p + 0.1, h, l, p)
        )
        if f is not None:
            assert f.ma == pytest.approx(pd.Series(close[: i + 1]).tail(20).mean())
            assert f.std == pytest.approx(pd.Series(close[: i + 1]).tail(20).std(ddof=0))
            assert f.atr == pytest.approx(np.mean(tr[-14:]))


def test_registry_can_construct_member_without_modifying_existing_portfolio():
    import json
    from datetime import UTC, datetime
    from pathlib import Path

    from strategy_python.mean_reversion_short_v2_5min import MeanReversionShortV2Strategy
    from strategy_python.portfolio import create_strategies, parse_portfolio

    value = json.loads(
        Path("strategy_python/_portfolio/mean_reversion_long_trend_both.json").read_text()
    )
    value["strategies"] = [
        {"id": "MRS2", "strategy": "mean_reversion_short_v2_5min", "parameters": {}}
    ]
    members, budget = create_strategies(datetime(2021, 1, 1, tzinfo=UTC), parse_portfolio(value))
    assert isinstance(members[0], MeanReversionShortV2Strategy) and budget is None
    assert members[0].config.max_notional_fraction == 3.0
    value["risk"] = {"mode": "shared", "total_fraction": 0.1}
    value["strategies"][0]["risk_weight"] = 1
    with pytest.raises(ValueError, match="尚未接入共享风险预算"):
        create_strategies(datetime(2021, 1, 1, tzinfo=UTC), parse_portfolio(value))
