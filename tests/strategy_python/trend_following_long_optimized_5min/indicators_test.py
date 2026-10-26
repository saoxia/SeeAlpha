import pytest

from strategy_python.trend_following_long_optimized_5min.bars import (
    NANOSECONDS_PER_MINUTE,
    CompletedBarAggregator,
    FiveMinuteAggregator,
)
from strategy_python.trend_following_long_optimized_5min.indicators import (
    HourlyTrendModel,
    MaDeviationModel,
)


def test_levels_use_each_bars_contemporaneous_ma_and_population_std() -> None:
    model = MaDeviationModel(
        ma_period=3,
        deviation_lookback=2,
        first_exit_std=0.2,
        final_exit_std=0.3,
    )
    assert model.update(11, 9, 10) is None
    assert model.update(12, 10, 11) is None
    assert model.update(14, 10, 12) is None
    levels = model.update(13, 8, 13)

    assert levels is not None
    assert levels.ma == 12
    assert levels.std == pytest.approx((2 / 3) ** 0.5)
    assert levels.long_entry == 15
    assert levels.short_entry == 8
    assert levels.long_first_exit == pytest.approx(12 + 0.2 * (2 / 3) ** 0.5)
    assert levels.long_final_exit == pytest.approx(12 - 0.3 * (2 / 3) ** 0.5)


def test_hourly_filter_requires_price_and_ma_slope_to_agree() -> None:
    model = HourlyTrendModel(period=3, slope_lookback=2)

    assert [model.update(close) for close in (1, 2, 3, 4)] == [0, 0, 0, 0]
    assert model.update(5) == 1
    assert model.update(0) == 0
    assert model.update(-1) == -1


def test_default_uses_ma20_with_separate_52_bar_deviation_window():
    model = MaDeviationModel()
    assert model.ma_period == 20
    assert model.deviation_lookback == 52
    for close in range(70):
        assert model.update(close, close, close) is None
    assert model.update(70, 70, 70) is not None
    assert HourlyTrendModel().period == 20


def test_only_emits_complete_five_minute_bar() -> None:
    aggregator = FiveMinuteAggregator()
    completed = None
    for minute in range(5):
        completed = aggregator.update(
            (minute + 1) * NANOSECONDS_PER_MINUTE - 1_000_000,
            high=101 + minute,
            low=99 - minute,
            close=100 + minute,
        )
        if minute < 4:
            assert completed is None

    assert completed is not None
    assert completed.high == 105
    assert completed.low == 95
    assert completed.close == 104


def test_hourly_bar_can_aggregate_twelve_five_minute_inputs() -> None:
    aggregator = CompletedBarAggregator(60, source_minutes=5)
    completed = None
    for index in range(12):
        completed = aggregator.update(
            (index + 1) * 5 * NANOSECONDS_PER_MINUTE - 1_000_000,
            high=101 + index,
            low=99 - index,
            close=100 + index,
        )
    assert completed is not None
    assert completed.high == 112
    assert completed.low == 88


def test_ma_slope_uses_adjacent_complete_windows_and_resets():
    model = MaDeviationModel(ma_period=3, deviation_lookback=1)
    assert model.update(1, 1, 1) is None
    assert model.update(2, 2, 2) is None
    assert model.update(3, 3, 3).ma_slope is None
    assert model.update(4, 4, 4).ma_slope == 1
    assert model.update(2, 2, 2).ma_slope == 0
    assert model.update(0, 0, 0).ma_slope == -1
    model.reset()
    assert model.update(1, 1, 1) is None
    assert model.update(2, 2, 2) is None
    assert model.update(3, 3, 3).ma_slope is None


@pytest.mark.parametrize("mirror", [False, True])
def test_hourly_slope_change_allows_slowing_trend_with_price_confirmation(mirror):
    model = HourlyTrendModel(period=3, slope_lookback=2, use_slope_change=True)
    original = HourlyTrendModel(period=3, slope_lookback=2)
    prices = [20, 17, 14, 12, 11, 13]
    if mirror:
        prices = [40 - p for p in prices]
    for p in prices[:-1]:
        assert model.update(p) == 0
        original.update(p)
    assert model.update(prices[-1]) == (-1 if mirror else 1)
    assert original.update(prices[-1]) == 0
    model.reset()
    assert not model.initialized and model.direction == 0


@pytest.mark.parametrize("prices", [[20, 17, 14, 12, 11, 10], [1, 2, 3, 4, 5, 6]])
def test_hourly_slope_change_rejects_wrong_price_side_or_equal_slopes(prices):
    model = HourlyTrendModel(period=3, slope_lookback=2, use_slope_change=True)
    for p in prices:
        direction = model.update(p)
    assert model.initialized and direction == 0


def test_hourly_slope_change_also_allows_acceleration():
    model = HourlyTrendModel(period=3, slope_lookback=2, use_slope_change=True)
    for p in [1, 2, 3, 5, 8, 13]:
        direction = model.update(p)
    assert direction == 1


@pytest.mark.parametrize("mirror", [False, True])
@pytest.mark.parametrize("last,allowed", [(1002.2, True), (1002, False), (1001.8, False)])
def test_adverse_hourly_slope_cap_is_directional_and_strict(mirror, last, allowed):
    capped = HourlyTrendModel(
        period=2, slope_lookback=1, use_slope_change=True, max_adverse_ma_return=0.001
    )
    original = HourlyTrendModel(period=2, slope_lookback=1, use_slope_change=True)
    for price in [1008, 1004, 996, last]:
        price = 2000 - price if mirror else price
        result = capped.update(price)
        old_result = original.update(price)
    expected = -1 if mirror else 1
    assert old_result == expected
    assert result == (expected if allowed else 0)


def test_adverse_cap_does_not_reject_strong_favorable_slope():
    model = HourlyTrendModel(
        period=3, slope_lookback=2, use_slope_change=True, max_adverse_ma_return=0.001
    )
    for price in [1, 2, 3, 5, 8, 13]:
        result = model.update(price)
    assert result == 1


@pytest.mark.parametrize("cap", [0, -1, float("nan"), float("inf")])
def test_adverse_cap_rejects_invalid_threshold(cap):
    with pytest.raises(ValueError):
        HourlyTrendModel(max_adverse_ma_return=cap)


def test_ma_cross_emits_only_fresh_crossings_and_resets():
    from strategy_python.trend_following_long_optimized_5min.indicators import MaCrossModel

    model = MaCrossModel(fast=2, slow=3)
    assert [model.update(x) for x in [3, 2, 1]] == [0, 0, 0]
    assert model.update(5) == 1
    assert model.update(6) == 0
    assert model.update(0) == -1
    assert model.update(-1) == 0
    model.reset()
    assert model.previous_difference is None and not model.closes


def test_ma_cross_from_equal_to_above():
    from strategy_python.trend_following_long_optimized_5min.indicators import MaCrossModel

    model = MaCrossModel()
    assert all(model.update(100) == 0 for _ in range(99))
    assert model.update(101) == 1
    assert model.update(102) == 0


def test_ma52_cross_uses_52_bar_fast_average():
    from strategy_python.trend_following_long_optimized_5min.indicators import MaCrossModel

    model = MaCrossModel(fast=52)
    for n in range(99):
        assert model.update(2000 - n) == 0
    prices = list(range(2000, 1901, -1))
    previous = sum(prices[-52:]) / 52 - sum(prices[-99:]) / 99
    signals = []
    for n in range(100):
        close = 1900 + n * 2
        prices.append(close)
        difference = sum(prices[-52:]) / 52 - sum(prices[-99:]) / 99
        expected = 1 if previous <= 0 < difference else -1 if previous >= 0 > difference else 0
        assert model.update(close) == expected
        signals.append(expected)
        previous = difference
    assert signals.count(1) == 1


def test_ma20_52_cross_matches_independent_rolling_means():
    from strategy_python.trend_following_long_optimized_5min.indicators import MaCrossModel

    model = MaCrossModel(fast=20, slow=52)
    prices = []
    previous = None
    signals = []
    for close in list(range(2000, 1900, -1)) + list(range(1900, 2050)):
        prices.append(close)
        expected = 0
        if len(prices) >= 52:
            difference = sum(prices[-20:]) / 20 - sum(prices[-52:]) / 52
            if previous is not None:
                expected = (
                    1 if previous <= 0 < difference else -1 if previous >= 0 > difference else 0
                )
            previous = difference
        assert model.update(close) == expected
        signals.append(expected)
    assert signals.count(1) == 1
