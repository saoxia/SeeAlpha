from collections import deque

import pytest

from strategy_python.mean_reversion_short_5min.indicators import RollingMaDistance

STEP = 5 * 60 * 1_000_000_000


def test_distance_uses_each_bars_own_ma_and_full_window():
    indicator = RollingMaDistance()
    for i in range(673):
        indicator.update((i + 1) * STEP, float(i))
    assert indicator.quantile(.89) is None
    indicator.update(674 * STEP, 673.)
    assert len(indicator.samples) == 576
    assert indicator.current_ma == 624.
    assert indicator.quantile(.11) == indicator.quantile(.89) == 49.
    indicator.update(675 * STEP, 600.)
    assert len(indicator.samples) == 576
    assert indicator.samples[-1][1] < 0
    assert indicator.samples[0][0] == 100 * STEP
    indicator.reset()
    assert indicator.current_ma is None and not indicator.samples


@pytest.mark.parametrize('fraction,expected', [(.11, 63.), (.89, 512.)])
def test_quantiles_have_distinct_ascending_order_meanings(fraction, expected):
    indicator = RollingMaDistance()
    indicator.samples = deque((i * STEP, float(i)) for i in range(576))
    assert indicator.quantile(fraction) == expected


def test_gap_requires_fresh_history():
    indicator = RollingMaDistance()
    for i in range(674):
        indicator.update((i + 1) * STEP, float(i))
    indicator.update(676 * STEP, 10.)
    assert indicator.current_ma is None
    assert indicator.quantile(.89) is None


@pytest.mark.parametrize('fraction', [.11, .89])
def test_entry_gate_compares_signed_distance_and_allows_equality(fraction):
    from strategy_python.mean_reversion_short_5min.features import EthFeatureModel
    from tests.strategy_python.mean_reversion_short_5min.strategy_test import make_config

    features = EthFeatureModel(make_config(entry_ma_distance_quantile=fraction))
    assert not features.entry_distance_allowed(100.)
    features.ma_distance.current_ma = 100.
    features.ma_distance.samples = deque((i * STEP, -10.) for i in range(576))
    assert not features.entry_distance_allowed(89.)
    assert features.entry_distance_allowed(90.)
    assert features.entry_distance_allowed(91.)


def test_newly_failing_filter_cancels_existing_initial_order():
    from strategy_python.mean_reversion_short_5min import MeanReversionShort5MinStrategy
    from tests.strategy_python.mean_reversion_short_5min.strategy_test import make_config

    cancellations = []

    class Observe(MeanReversionShort5MinStrategy):
        def _cancel_entry(self, state):
            cancellations.append(True)

        def apply_entry_target(self, state, target):
            pytest.fail('Filtered entry must not be submitted')

    strategy = Observe(make_config(entry_ma_distance_quantile=.89))
    strategy.features.bollinger = (90., 100., 110.)
    strategy.features.ma_distance.current_ma = 100.
    strategy.features.ma_distance.samples = deque((i * STEP, 20.) for i in range(576))
    strategy._refresh_initial_order(strategy.trade_state, STEP)
    assert cancellations == [True]


def test_default_filter_uses_64th_smallest_of_576_samples():
    from strategy_python.mean_reversion_short_5min.config import MeanReversionShort5MinConfig
    from strategy_python.mean_reversion_short_5min.features import EthFeatureModel
    from tests.strategy_python.mean_reversion_short_5min.strategy_test import (
        INSTRUMENT_ID,
        MINUTE_BAR_TYPE,
    )

    config = MeanReversionShort5MinConfig(instrument_id=INSTRUMENT_ID, bar_type=MINUTE_BAR_TYPE)
    assert config.entry_ma_distance_quantile == .11
    features = EthFeatureModel(config)
    assert not features.entry_distance_allowed(1000.)
    features.ma_distance.current_ma = 1000.
    # Reverse input order to ensure the cutoff is based on sorted signed distances.
    features.ma_distance.samples = deque((i * STEP, float(576 - i)) for i in range(576))
    assert features.ma_distance.quantile(.11) == 64.
    assert not features.entry_distance_allowed(1063.99)
    assert features.entry_distance_allowed(1064.)
    assert features.entry_distance_allowed(1064.01)


def test_p50_p90_mean_uses_all_samples_inside_value_boundaries_including_ties():
    indicator = RollingMaDistance()
    assert indicator.range_mean(.5, .9) is None
    values = [-2.] * 288 + [2.] * 100 + [8.] * 130 + [20.] * 58
    indicator.samples = deque((i * STEP, value) for i, value in enumerate(reversed(values)))
    assert indicator.quantile(.5) == 2.
    assert indicator.quantile(.9) == 20.
    assert indicator.range_mean(.5, .9) == pytest.approx(25 / 3)
    assert indicator.range_mean(.5, .9) != 11.  # Not the average of the two endpoints.


def test_p50_p90_mean_preserves_negative_distances():
    indicator = RollingMaDistance()
    indicator.samples = deque((i * STEP, -7.) for i in range(576))
    assert indicator.range_mean(.5, .9) == -7.


def test_p31_p35_range_mean_uses_samples_not_just_endpoints():
    indicator = RollingMaDistance()
    values = [-10.] * 178 + [-2.] * 20 + [4.] * 4 + [10.] * 374
    indicator.samples = deque((i * STEP, value) for i, value in enumerate(values))
    assert indicator.quantile(.31) == -2.
    assert indicator.quantile(.35) == 4.
    assert indicator.range_mean(.31, .35) == -1.


def test_p80_p95_range_mean_includes_boundary_ties():
    from collections import deque

    indicator = RollingMaDistance()
    values = [-10.] * 460 + [2.] * 70 + [20.] * 18 + [30.] * 28
    indicator.samples = deque(enumerate(values))
    assert indicator.quantile(.80) == 2.
    assert indicator.quantile(.95) == 20.
    assert indicator.range_mean(.80, .95) == pytest.approx(500 / 88)


def test_negative_only_p20_p70_filters_before_ranking_and_excludes_zero():
    indicator = RollingMaDistance()
    values = list(map(float, range(-10, 0))) + [0.] * 300 + [100.] * 266
    indicator.samples = deque(enumerate(reversed(values)))
    # Ten negatives: P20 is -8 (rank 3), P70 is -3 (rank 8).
    assert indicator.range_mean(.2, .7, negative_only=True) == -5.5
    assert indicator.range_mean(.2, .7) != -5.5


def test_negative_only_range_includes_all_boundary_ties():
    indicator = RollingMaDistance()
    values = [-10.] * 3 + [-5.] * 4 + [-1.] * 3 + [0.] * 566
    indicator.samples = deque(enumerate(values))
    # P20=-10, P70=-1: all duplicate values at both boundaries are included.
    assert indicator.range_mean(.2, .7, negative_only=True) == -5.3


def test_negative_only_range_handles_empty_single_and_unready_windows():
    indicator = RollingMaDistance()
    indicator.samples = deque(enumerate([-2.] * 575))
    assert indicator.range_mean(.2, .7, negative_only=True) is None
    indicator.samples = deque(enumerate([0.] * 300 + [1.] * 276))
    assert indicator.range_mean(.2, .7, negative_only=True) is None
    indicator.samples = deque(enumerate([-2.] + [0.] * 575))
    assert indicator.range_mean(.2, .7, negative_only=True) == -2.
