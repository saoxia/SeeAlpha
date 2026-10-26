from datetime import UTC, datetime
from decimal import Decimal

import pytest
from nautilus_trader.model import BarType, InstrumentId, OmsType

from strategy_python._report import backtest_report_dir
from strategy_python.mean_reversion_long_optimized_5min import (
    MeanReversionLongOptimized5MinConfig,
    MeanReversionLongOptimized5MinStrategy,
    backtest,
)
from strategy_python.mean_reversion_long_optimized_5min.bars import (
    NANOSECONDS_PER_DAY,
    NANOSECONDS_PER_HOUR,
    NANOSECONDS_PER_MINUTE,
    CompletedBar,
    CompletedBarAggregator,
)
from strategy_python.mean_reversion_long_optimized_5min.indicators import RollingBollinger
from strategy_python.mean_reversion_long_optimized_5min.risk import (
    DirectionalRiskModel,
    RecoveryModel,
    RecoveryParameters,
    RiskParameters,
)
from strategy_python.mean_reversion_long_optimized_5min.signal import EthEntrySignalModel
from strategy_python.mean_reversion_long_optimized_5min.state import DirectionState

INSTRUMENT_ID = InstrumentId.from_str("ETHUSDT-PERP.BINANCE")
MINUTE_BAR_TYPE = BarType.from_str("ETHUSDT-PERP.BINANCE-1-MINUTE-LAST-EXTERNAL")


def make_config(**overrides: object) -> MeanReversionLongOptimized5MinConfig:
    values = {
        "instrument_id": INSTRUMENT_ID,
        "bar_type": MINUTE_BAR_TYPE,
        **overrides,
    }
    return MeanReversionLongOptimized5MinConfig(**values)


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


def test_strategy_builds_with_formal_long_only_defaults() -> None:
    strategy = MeanReversionLongOptimized5MinStrategy(make_config())

    assert strategy.trade_state.direction == 1
    assert not hasattr(strategy.config, "mode")
    assert strategy.config.max_units == 4
    assert strategy.config.cycle_risk_fraction == 0.30
    assert strategy.config.hard_stop_distance == 0.25
    assert strategy.legs == (strategy.trade_state,)


def test_strategy_rejects_input_coarser_than_signal() -> None:
    bar_type = BarType.from_str("ETHUSDT-PERP.BINANCE-15-MINUTE-LAST-EXTERNAL")
    with pytest.raises(ValueError, match="整除 5 分钟"):
        MeanReversionLongOptimized5MinStrategy(make_config(bar_type=bar_type))


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


def test_double_valley_boundary_control_points() -> None:
    model = DirectionalRiskModel(RiskParameters())
    expected = {
        0.00: 1.0,
        0.17: 0.1,
        0.34: 1.0,
        0.50: 1.0,
        0.66: 1.0,
        0.83: 0.1,
        1.00: 1.0,
    }

    for position, coefficient in expected.items():
        assert model.boundary_coefficient(1, position) == pytest.approx(coefficient)


def test_strength_curve_knots_and_cap() -> None:
    model = DirectionalRiskModel(RiskParameters())
    expected = {0.55: 0.75, 0.62: 0.80, 0.80: 0.90, 1.00: 1.05, 1.40: 1.30, 2.0: 1.30}

    for strength, coefficient in expected.items():
        assert model.strength_coefficient(strength) == pytest.approx(coefficient)


def test_planned_entries_are_based_on_each_new_average() -> None:
    model = DirectionalRiskModel(RiskParameters())

    prices = model.planned_entry_prices(1, 100.0)

    assert prices[0] == 100.0
    assert prices[1] == pytest.approx(99.5)
    assert prices[2] == pytest.approx(((100 + 99.5) / 2) * 0.99)
    assert prices[3] == pytest.approx((sum(prices[:3]) / 3) * 0.98)


def test_initial_sizing_floors_to_instrument_increment() -> None:
    model = DirectionalRiskModel(RiskParameters())
    model.cycle_context = lambda *args, **kwargs: {  # type: ignore[method-assign]
        "risk_fraction": 0.30,
        "strength": 1.0,
        "strength_coefficient": 1.05,
        "boundary_coefficient": 1.0,
        "recovery_coefficient": 1.0,
        "position": 0.5,
    }

    sizing = model.size_initial_entry(
        direction=1,
        initial_price=100,
        now_ns=0,
        equity=Decimal(500),
        size_increment=Decimal("0.01"),
    )

    assert sizing is not None
    assert sizing["unit_quantity"] % Decimal("0.01") == 0
    assert sizing["planned_risk"] <= Decimal(150)


def test_add_cannot_exceed_locked_cycle_risk() -> None:
    model = DirectionalRiskModel(RiskParameters())
    state = DirectionState(
        quantity=Decimal(1),
        average_price=Decimal(100),
        unit_quantity=Decimal(1),
        cycle_risk_budget=Decimal(1),
    )

    assert not model.entry_preserves_cycle_risk(state, Decimal(1), Decimal("99.5"))


def test_six_hour_recovery_gate_is_directional() -> None:
    model = RecoveryModel(RecoveryParameters())
    state = DirectionState()
    model.reset_after_hard_stop(state, 0)

    assert not model.on_six_hour_bar(
        state,
        completed_bar(6 * NANOSECONDS_PER_HOUR, high=102, low=99),
        ma52=100,
    )
    assert model.on_six_hour_bar(
        state,
        completed_bar(12 * NANOSECONDS_PER_HOUR, high=103, low=100),
        ma52=100,
    )
    assert not state.ma52_recovery_waiting


def test_long_daily_lu_window_is_inclusive_and_finite() -> None:
    model = RecoveryModel(RecoveryParameters())
    state = DirectionState()
    model.reset_after_hard_stop(state, 0)
    state.ma52_recovery_waiting = False
    state.ma52_recovery_released_ns = 10 * NANOSECONDS_PER_DAY

    assert model.risk_coefficient(state, int(40.99 * NANOSECONDS_PER_DAY)) == 1.0
    assert model.risk_coefficient(state, 41 * NANOSECONDS_PER_DAY) == 0.30
    assert model.risk_coefficient(state, 44 * NANOSECONDS_PER_DAY) == 0.30
    assert model.risk_coefficient(state, int(44.01 * NANOSECONDS_PER_DAY)) == 1.0


def test_daily_confirmation_immediately_releases_lu_risk() -> None:
    model = RecoveryModel(RecoveryParameters())
    state = DirectionState()
    model.reset_after_hard_stop(state, 0)
    state.ma52_recovery_waiting = False
    state.ma52_recovery_released_ns = 10 * NANOSECONDS_PER_DAY
    now = 42 * NANOSECONDS_PER_DAY
    assert model.risk_coefficient(state, now) == 0.30

    bar = completed_bar(now, high=120, low=101, close=110, minutes=1440)
    assert model.on_daily_bar(state, bar, ma52=100)
    assert model.risk_coefficient(state, now) == 1.0


def test_clearing_cycle_preserves_recovery_lock_and_cooldown() -> None:
    state = DirectionState(
        quantity=Decimal(1),
        average_price=Decimal(100),
        cooldown_until_ns=123,
        ma52_recovery_waiting=True,
    )

    state.clear_cycle()

    assert not state.invested
    assert state.cooldown_until_ns == 123
    assert state.ma52_recovery_waiting


@pytest.mark.parametrize("mode", ["long_only", "short_only", "both"])
def test_direction_cannot_be_selected_in_config(mode: str) -> None:
    with pytest.raises(TypeError):
        make_config(mode=mode)


def test_reset_keeps_fixed_direction_and_clears_position() -> None:
    strategy = MeanReversionLongOptimized5MinStrategy(make_config())
    strategy.trade_state.quantity = Decimal(1)
    strategy.on_reset()
    assert strategy.trade_state.direction == 1
    assert not strategy.trade_state.invested
    assert strategy.legs == (strategy.trade_state,)
    with pytest.raises(AttributeError):
        strategy.trade_state.direction = -1


def test_risk_model_rejects_opposite_direction() -> None:
    model = DirectionalRiskModel(RiskParameters())
    with pytest.raises(ValueError):
        model.planned_entry_prices(-1, 100)
    with pytest.raises(ValueError):
        model.cycle_context(-1, 100, 0)


def test_signal_respects_warmup_recovery_and_ma_filter() -> None:
    model = EthEntrySignalModel(make_config(trade_start_ns=100))
    state = DirectionState()
    bollinger = (90.0, 100.0, 110.0)

    assert model.evaluate(state, 99, bollinger, 0.5) is None
    assert model.evaluate(state, 100, bollinger, None) is None
    assert model.evaluate(state, 100, bollinger, 0.7) is None

    state.ma52_recovery_waiting = True
    assert model.evaluate(state, 100, bollinger, 0.5) is None


def test_signal_uses_fixed_direction_and_boundary() -> None:
    signal = EthEntrySignalModel(make_config()).evaluate(
        DirectionState(), 1, (90.0, 100.0, 110.0), 0.5
    )
    assert signal is not None
    assert signal.direction == 1
    assert signal.price == 90.0


def test_backtest_passes_strategy_and_own_report_directory(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(
        "strategy_python._backtesting.run_backtest",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )
    start = datetime(2026, 8, 1, tzinfo=UTC)
    end = datetime(2026, 8, 2, tzinfo=UTC)
    backtest.run_backtest(start, end)
    ((args, kwargs),) = calls
    actual_start, actual_end, strategy, name, spec = args
    assert (actual_start, actual_end) == (start, end)
    assert isinstance(strategy, MeanReversionLongOptimized5MinStrategy)
    assert strategy.config.trade_start_ns == int(start.timestamp() * 1_000_000_000)
    assert strategy.trade_state.direction == 1
    assert spec.instrument_id == strategy.config.instrument_id == INSTRUMENT_ID
    assert spec.bar_type == strategy.config.bar_type == MINUTE_BAR_TYPE
    assert spec.oms_type == OmsType.HEDGING
    assert spec.warmup_days == 135
    assert name == "mean_reversion_long_optimized_5min"
    assert kwargs["report_dir"] == backtest_report_dir("mean_reversion_long_optimized_5min")


def test_cli_rejects_direction_switch(monkeypatch) -> None:
    monkeypatch.setattr("sys.argv", ["backtest", "--strategy-mode", "both"])
    with pytest.raises(SystemExit) as error:
        backtest.main()
    assert error.value.code == 2
