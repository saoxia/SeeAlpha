from datetime import UTC, datetime
from decimal import Decimal

import pytest
from nautilus_trader.model import BarType, InstrumentId, OmsType

from strategy_python.trend_following_short_optimized_5min import (
    TrendFollowingShortOptimized5MinConfig,
    TrendFollowingShortOptimized5MinStrategy,
)
from strategy_python.trend_following_short_optimized_5min.backtest import (
    BACKTEST_SPEC,
    create_strategy,
)
from strategy_python.trend_following_short_optimized_5min.sizing import equity_quantity

INSTRUMENT_ID = InstrumentId.from_str("ETHUSDT-PERP.BINANCE")
BAR_TYPE = BarType.from_str("ETHUSDT-PERP.BINANCE-5-MINUTE-LAST-EXTERNAL")


def make_config(**overrides: object) -> TrendFollowingShortOptimized5MinConfig:
    overrides.setdefault("peak_profit_exit", False)
    overrides.setdefault("take_profit_activation_return", None)
    overrides.setdefault("native_stop_loss", False)
    return TrendFollowingShortOptimized5MinConfig(
        instrument_id=INSTRUMENT_ID,
        bar_type=overrides.pop("bar_type", BAR_TYPE),
        **overrides,
    )


def test_strategy_has_only_its_own_direction():
    strategy = TrendFollowingShortOptimized5MinStrategy(make_config())
    assert [leg.direction for leg in strategy.legs] == [-1]
    assert not hasattr(strategy, "long")
    strategy.on_reset()
    assert [leg.direction for leg in strategy.legs] == [-1]


@pytest.mark.parametrize("mode", ["both", "long_only", "short_only"])
def test_rejects_direction_switch(mode):
    with pytest.raises(TypeError):
        make_config(mode=mode)


def test_rejects_input_coarser_than_signal() -> None:
    bar_type = BarType.from_str("ETHUSDT-PERP.BINANCE-15-MINUTE-LAST-EXTERNAL")
    with pytest.raises(ValueError, match="整除 5 分钟"):
        TrendFollowingShortOptimized5MinStrategy(make_config(bar_type=bar_type))


@pytest.mark.parametrize("direction,half", [(-1, False)])
def test_current_scheme_exit_behavior_in_both_mode(direction, half):
    from strategy_python.trend_following_short_optimized_5min.indicators import TrendLevels

    class ProbeStrategy(TrendFollowingShortOptimized5MinStrategy):
        def cancel_entry(self, state):
            pass

        def submit_exit(self, state, *, half):
            self.exit_half = half

    strategy = ProbeStrategy(make_config())
    state = strategy.leg
    state.quantity = Decimal(1)
    levels = TrendLevels(100, 10, 120, 80, 102, 97, 98, 103)
    strategy._refresh_leg(state, levels, 100)
    assert strategy.exit_half is half
    if direction == 1:
        state.first_exit_done = True
        strategy._refresh_leg(state, levels, 96)
        assert not strategy.exit_half


def test_uses_full_equity_and_floors_to_size_increment() -> None:
    assert equity_quantity(
        Decimal(1000),
        Decimal(333),
        Decimal(1),
        Decimal("0.01"),
    ) == Decimal("3.00")


def test_supports_three_times_equity_position_sizing() -> None:
    strategy = TrendFollowingShortOptimized5MinStrategy(make_config(equity_fraction_per_side=3.0))
    assert equity_quantity(
        Decimal(500),
        Decimal(2000),
        Decimal(str(strategy.config.equity_fraction_per_side)),
        Decimal("0.001"),
    ) == Decimal("0.750")


@pytest.mark.parametrize("fraction", [0.0, -1.0, float("inf"), float("nan")])
def test_rejects_invalid_equity_multiplier(fraction) -> None:
    with pytest.raises(ValueError, match="equity_fraction_per_side"):
        TrendFollowingShortOptimized5MinStrategy(make_config(equity_fraction_per_side=fraction))


def test_backtest_uses_hedging_and_offline_data() -> None:
    assert BACKTEST_SPEC.oms_type == OmsType.HEDGING
    assert BACKTEST_SPEC.warmup_days >= create_strategy().config.daily_boll_period + 1
    assert not BACKTEST_SPEC.prepare_data_before_run
    assert isinstance(create_strategy(), TrendFollowingShortOptimized5MinStrategy)


def test_backtest_passes_start_to_strategy():
    start = datetime(2026, 8, 1, tzinfo=UTC)
    strategy = create_strategy(start)
    assert strategy.config.trade_start_ns == int(start.timestamp() * 1_000_000_000)
    assert [leg.direction for leg in strategy.legs] == [-1]


@pytest.mark.parametrize("direction,slope", [(-1, 0), (-1, 1)])
@pytest.mark.parametrize("first_done", [False, True])
def test_slope_exit_closes_all_before_or_after_partial_exit(direction, slope, first_done):
    from strategy_python.trend_following_short_optimized_5min.indicators import TrendLevels

    class ProbeStrategy(TrendFollowingShortOptimized5MinStrategy):
        def cancel_entry(self, state):
            self.cancelled = True

        def submit_exit(self, state, *, half):
            self.exit_half = half

    strategy = ProbeStrategy(
        make_config(
            exit_on_ma_slope_reversal=True,
            tighten_exits_with_slope_risk=False,
        )
    )
    state = strategy.leg
    state.quantity = Decimal(1)
    state.first_exit_done = first_done
    # Price alone does not hit either exit line.
    levels = TrendLevels(100, 10, 120, 80, 102, 97, 98, 103, ma_slope=slope)
    strategy._refresh_leg(state, levels, 105 if direction == 1 else 95)
    assert strategy.cancelled
    assert strategy.exit_half is False


@pytest.mark.parametrize("direction", [-1])
def test_slope_exit_can_be_disabled_and_does_not_exit_favorable_slope(direction):
    from strategy_python.trend_following_short_optimized_5min.indicators import TrendLevels

    class ProbeStrategy(TrendFollowingShortOptimized5MinStrategy):
        def submit_exit(self, state, *, half):
            raise AssertionError("Unexpected exit")

    for enabled, slope in [(False, 0), (True, direction)]:
        strategy = ProbeStrategy(
            make_config(
                exit_on_ma_slope_reversal=enabled,
                tighten_exits_with_slope_risk=False,
            )
        )
        state = strategy.leg
        state.quantity = Decimal(1)
        levels = TrendLevels(100, 10, 120, 80, 102, 97, 98, 103, ma_slope=slope)
        strategy._refresh_leg(state, levels, 105 if direction == 1 else 95)


@pytest.mark.parametrize("direction", [-1])
def test_strategy_routes_loss_stop_to_full_market_exit(direction):
    from strategy_python.trend_following_short_optimized_5min.indicators import TrendLevels

    class ProbeStrategy(TrendFollowingShortOptimized5MinStrategy):
        def cancel_entry(self, state):
            self.cancelled = True

        def submit_exit(self, state, *, half):
            self.exit_half = half

    strategy = ProbeStrategy(make_config())
    state = strategy.leg
    state.record_entry_fill(Decimal(1), Decimal(100))
    levels = TrendLevels(100, 10, 120, 80, 90, 80, 110, 120)
    strategy._refresh_leg(state, levels, 99.49 if direction == 1 else 100.51)
    assert strategy.cancelled and strategy.exit_half is False


@pytest.mark.parametrize("threshold", [0, -0.1, 1, float("inf"), float("nan")])
def test_invalid_loss_limit_rejected(threshold):
    with pytest.raises(ValueError, match="亏损阈值"):
        TrendFollowingShortOptimized5MinStrategy(make_config(max_loss_return=threshold))


def test_production_defaults_preserve_loss_stop_and_enable_selected_activation():
    strategy = create_strategy()
    assert strategy.config.native_stop_loss
    assert strategy.config.max_loss_return == 0.005
    assert strategy.config.take_profit_activation_return == 0.04
    assert not strategy.config.tighten_exits_with_slope_risk


@pytest.mark.parametrize("value", [0, -0.01, float("inf"), float("nan")])
def test_invalid_profit_activation_threshold_rejected(value):
    with pytest.raises(ValueError, match="止盈启动阈值"):
        TrendFollowingShortOptimized5MinStrategy(make_config(take_profit_activation_return=value))


@pytest.mark.parametrize("direction", [-1])
def test_single_exit_closes_full_position_at_first_line(direction):
    from strategy_python.trend_following_short_optimized_5min.indicators import TrendLevels

    class ProbeStrategy(TrendFollowingShortOptimized5MinStrategy):
        def cancel_entry(self, state):
            pass

        def submit_exit(self, state, *, half):
            self.exit_half = half

    strategy = ProbeStrategy(make_config(short_full_exit_on_first=True))
    state = strategy.leg
    state.quantity = Decimal(1)
    strategy._refresh_leg(state, TrendLevels(100, 10, 120, 80, 102, 97, 98, 103), 100)
    assert strategy.exit_half is False


def test_entry_ma99_leaves_exit_ma20_and_sigma_unchanged():
    from strategy_python.trend_following_short_optimized_5min.indicators import MaDeviationModel

    strategy = TrendFollowingShortOptimized5MinStrategy(make_config(entry_ma_period=99))
    original = MaDeviationModel(20, 52)
    entry = MaDeviationModel(99, 52)
    for n in range(170):
        close = 2000 + n * 0.5 + (n % 7)
        combined = strategy._update_levels(close + 2, close - 3, close)
        base = original.update(close + 2, close - 3, close)
        expected = entry.update(close + 2, close - 3, close)
        if n < 149:
            assert combined is None
        else:
            assert combined.long_entry == expected.long_entry
            assert combined.short_entry == expected.short_entry
            for name in (
                "ma",
                "std",
                "long_first_exit",
                "long_final_exit",
                "short_first_exit",
                "short_final_exit",
                "ma_slope",
            ):
                assert getattr(combined, name) == getattr(base, name)
    strategy.on_reset()
    assert not strategy.levels.initialized and not strategy.entry_levels.initialized


def test_explicit_ma20_entry_model_reuses_original_ma20():
    strategy = TrendFollowingShortOptimized5MinStrategy(make_config(entry_ma_period=20))
    assert strategy.entry_levels is strategy.levels


def test_ma99_exit_replaces_both_lines_without_changing_rearm_ma20():
    from strategy_python.trend_following_short_optimized_5min.indicators import MaDeviationModel

    strategy = TrendFollowingShortOptimized5MinStrategy(make_config(exit_on_ma99=True))
    baseline = MaDeviationModel(20, 52)
    for n in range(160):
        close = 2000 + n
        result = strategy._update_levels(close + 2, close - 2, close)
        original = baseline.update(close + 2, close - 2, close)
    expected = sum(2000 + n for n in range(61, 160)) / 99
    assert result.ma == original.ma
    assert result.std == original.std
    assert result.long_first_exit == result.long_final_exit == expected
    assert result.short_first_exit == result.short_final_exit == expected
    strategy.on_reset()
    assert not strategy.ma99_exit_model.initialized


def test_ma50_exit_uses_requested_period():
    strategy = TrendFollowingShortOptimized5MinStrategy(
        make_config(exit_on_ma99=True, exit_ma_period=50)
    )
    for n in range(160):
        result = strategy._update_levels(2002 + n, 1998 + n, 2000 + n)
    expected = sum(2000 + n for n in range(110, 160)) / 50
    assert result.long_first_exit == result.long_final_exit == expected
    assert result.short_first_exit == result.short_final_exit == expected
