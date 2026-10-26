from datetime import UTC, datetime
from decimal import Decimal

import pytest
from nautilus_trader.model import BarType, InstrumentId, OmsType

from strategy_python._report import backtest_report_dir
from strategy_python.mean_reversion_short_5min import (
    MeanReversionShort5MinConfig,
    MeanReversionShort5MinStrategy,
    backtest,
)
from strategy_python.mean_reversion_short_5min.bars import (
    NANOSECONDS_PER_MINUTE,
    CompletedBar,
    CompletedBarAggregator,
)
from strategy_python.mean_reversion_short_5min.indicators import RollingBollinger
from strategy_python.mean_reversion_short_5min.signal import EthEntrySignalModel
from strategy_python.mean_reversion_short_5min.state import DirectionState

INSTRUMENT_ID = InstrumentId.from_str("ETHUSDT-PERP.BINANCE")


MINUTE_BAR_TYPE = BarType.from_str("ETHUSDT-PERP.BINANCE-1-MINUTE-LAST-EXTERNAL")


def make_config(**overrides: object) -> MeanReversionShort5MinConfig:
    values = {
        "instrument_id": INSTRUMENT_ID,
        "bar_type": MINUTE_BAR_TYPE,
        **overrides,
    }
    return MeanReversionShort5MinConfig(**values)


def completed_bar(
    end: int,
    *,
    high: float = 110,
    low: float = 90,
    close: float = 100,
    minutes: int = 360,
) -> CompletedBar:
    return CompletedBar(
        ts_start=end - minutes * NANOSECONDS_PER_MINUTE,
        ts_end=end,
        open=100,
        high=high,
        low=low,
        close=close,
    )


def test_strategy_rejects_input_coarser_than_signal() -> None:
    bar_type = BarType.from_str("ETHUSDT-PERP.BINANCE-15-MINUTE-LAST-EXTERNAL")
    with pytest.raises(ValueError, match="整除 5 分钟"):
        MeanReversionShort5MinStrategy(make_config(bar_type=bar_type))


def test_aggregator_emits_only_after_complete_utc_bucket() -> None:
    aggregator = CompletedBarAggregator(5)
    result = None
    for minute in range(5):
        end = (minute + 1) * NANOSECONDS_PER_MINUTE
        result = aggregator.update(
            end - 1_000_000,
            100 + minute,
            102 + minute,
            99 - minute,
            101 + minute,
        )
        if minute < 4:
            assert result is None

    assert result == CompletedBar(
        ts_start=0,
        ts_end=5 * NANOSECONDS_PER_MINUTE,
        open=100,
        high=106,
        low=95,
        close=105,
    )


def test_aggregator_rejects_incomplete_bucket_after_gap() -> None:
    aggregator = CompletedBarAggregator(5)
    for minute in (0, 1, 3, 4):
        end = (minute + 1) * NANOSECONDS_PER_MINUTE
        result = aggregator.update(end - 1_000_000, 100, 101, 99, 100)

    assert result is None


def test_bollinger_uses_population_standard_deviation() -> None:
    bollinger = RollingBollinger(4, 2.0)
    for value in (1, 2, 3):
        assert bollinger.update(value) is None

    lower, middle, upper = bollinger.update(4)
    assert middle == pytest.approx(2.5)
    assert lower == pytest.approx(2.5 - 2 * (1.25**0.5))
    assert upper == pytest.approx(2.5 + 2 * (1.25**0.5))


@pytest.mark.parametrize("mode", ["long_only", "short_only", "both"])
def test_direction_cannot_be_selected_in_config(mode: str) -> None:
    with pytest.raises(TypeError):
        make_config(mode=mode)


def test_reset_keeps_fixed_direction_and_clears_position() -> None:
    strategy = MeanReversionShort5MinStrategy(make_config())
    strategy.trade_state.quantity = Decimal(1)
    strategy.on_reset()
    assert strategy.trade_state.direction == -1
    assert not strategy.trade_state.invested
    assert strategy.legs == (strategy.trade_state,)
    with pytest.raises(AttributeError):
        strategy.trade_state.direction = 1


def test_signal_keeps_valid_price_and_no_duplicate_position_invariants() -> None:
    model = EthEntrySignalModel(make_config())
    state = DirectionState()
    for upper in (0, -1, float('nan'), float('inf')):
        assert model.evaluate(state, 100, (90, 100, upper)) is None
    state.quantity = Decimal(1)
    assert model.evaluate(state, 100, (90, 100, 110)) is None


def test_backtest_passes_strategy_and_own_report_directory(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(
        "strategy_python._backtesting.run_backtest", lambda *args, **kwargs: calls.append((args, kwargs))
    )
    start = datetime(2026, 8, 1, tzinfo=UTC)
    end = datetime(2026, 8, 2, tzinfo=UTC)
    backtest.run_backtest(start, end)
    ((args, kwargs),) = calls
    actual_start, actual_end, strategy, name, spec = args
    assert (actual_start, actual_end) == (start, end)
    assert isinstance(strategy, MeanReversionShort5MinStrategy)
    assert strategy.config.trade_start_ns == int(start.timestamp() * 1_000_000_000)
    assert strategy.trade_state.direction == -1
    assert spec.instrument_id == strategy.config.instrument_id == INSTRUMENT_ID
    assert spec.bar_type == strategy.config.bar_type == MINUTE_BAR_TYPE
    assert spec.oms_type == OmsType.HEDGING
    assert spec.warmup_days == 15
    assert name == "mean_reversion_short_5min"
    assert kwargs["report_dir"] == backtest_report_dir("mean_reversion_short_5min")


def test_cli_rejects_direction_switch(monkeypatch) -> None:
    monkeypatch.setattr("sys.argv", ["backtest", "--strategy-mode", "both"])
    with pytest.raises(SystemExit) as error:
        backtest.main()
    assert error.value.code == 2


def test_backtest_date_boundary_only_warms_indicators_before_start():
    class ObserveTargets(MeanReversionShort5MinStrategy):
        def _refresh_initial_order(self, state, now_ns):
            seen.append(now_ns)

    seen, updated = [], []
    strategy = ObserveTargets(make_config(trade_start_ns=100))

    def update(bar):
        updated.append(bar.ts_end)
        return (90, 100, 110)

    strategy.features.on_five_minute_bar = update
    strategy._on_five_minute_bar(completed_bar(99))
    assert seen == [] and updated == [99]
    strategy._on_five_minute_bar(completed_bar(100))
    assert seen == [100]


@pytest.mark.parametrize('field', ['ma_filter_enabled', 'ma_filter_period',
                                   'ma_filter_window', 'ma_reentry_ratio'])
def test_removed_ma99_configuration_is_not_silently_accepted(field):
    with pytest.raises(TypeError):
        make_config(**{field: 1})



@pytest.mark.parametrize('field', [
    'boundary_risk_enabled', 'strength_risk_enabled', 'risk_reference_days', 'risk_move_hours',
    'add_offsets', 'add_spacing_multiplier', 'multi_timeframe_adds_enabled',
    'fixed_boll_width_add_enabled', 'boll_stop_multiplier', 'hard_stop_distance',
    'account_loss_stop_fraction', 'max_holding_hours', 'max_position_hours',
    'time_stop_enabled', 'partial_take_profit_enabled', 'take_profit_rate',
    'take_profit_at_lower_band', 'ma_percentile_stop_enabled', 'flat_cooldown_minutes',
    'recovery_ma_period', 'recovery_snapshot_bars',
])
def test_removed_inactive_parameters_are_rejected(field):
    with pytest.raises(TypeError):
        make_config(**{field: 1})


def test_risk_only_sizes_short_positions():
    from strategy_python.mean_reversion_short_5min.risk import DirectionalRiskModel, RiskParameters

    with pytest.raises(ValueError):
        DirectionalRiskModel(RiskParameters()).size_initial_entry(
            1, 100, 0, Decimal(500), Decimal('.001'), second_entry_price=107)
