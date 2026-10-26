"""NautilusTrader 与 MA20 做多趋势跟随领域逻辑之间的编排层。"""

from dataclasses import replace
from decimal import ROUND_DOWN, Decimal
from math import isfinite

from nautilus_trader.model.data import Bar
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.trading.strategy import Strategy

from strategy_python._indicator.bars import source_seconds_for
from strategy_python._mechanism.rules import (
    HourlyATR,
    trend_snapshot,
)
from strategy_python.btc_cycle_optimized_20260915_tfl_original.bars import (
    CompletedBarAggregator,
)
from strategy_python.btc_cycle_optimized_20260915_tfl_original.config import (
    MechanismTrendFollowingLong5MinConfig,
)
from strategy_python.btc_cycle_optimized_20260915_tfl_original.execution import (
    TrendExecutionMixin,
)
from strategy_python.btc_cycle_optimized_20260915_tfl_original.indicators import (
    HourlyTrendModel,
    MaCrossModel,
    MaDeviationModel,
    TrendLevels,
)
from strategy_python.btc_cycle_optimized_20260915_tfl_original.risk import (
    SlopeRiskModel,
    profit_risk_multiplier,
    tightened_level,
)
from strategy_python.btc_cycle_optimized_20260915_tfl_original.signal import (
    ExitAction,
    evaluate_exit,
)
from strategy_python.btc_cycle_optimized_20260915_tfl_original.sizing import (
    equity_quantity,
)
from strategy_python.btc_cycle_optimized_20260915_tfl_original.state import (
    TrendLegState,
)


class MechanismTrendFollowingLong5MinStrategy(TrendExecutionMixin, Strategy):
    """只维护做多持仓账本，并在 5 分钟收盘后更新订单。"""

    def __init__(self, config: MechanismTrendFollowingLong5MinConfig) -> None:
        self._validate_config(config)
        if (
            config.max_positions_per_side != 1
            or config.entry_trigger != "ma_cross"
            or not config.peak_profit_exit
        ):
            raise ValueError("Mechanism trend supports one market-cross/peak-exit leg")
        if (
            min(
                config.mechanism_stop_atr,
                config.mechanism_activation_atr,
                config.mechanism_price_risk,
            )
            <= 0
        ):
            raise ValueError("Mechanism coefficients must be positive")
        # Explicit historical replay: training-period overlap is declared in run metadata.
        super().__init__(config)
        self.instrument: Instrument | None = None
        source_seconds = source_seconds_for(config.bar_type)
        self.five_minute = CompletedBarAggregator(config.cycle_signal_minutes, source_seconds=source_seconds)
        self.one_hour = CompletedBarAggregator(60, source_seconds=source_seconds)
        self.hourly_trend = HourlyTrendModel(
            config.trend_ma_period,
            config.trend_slope_lookback,
            use_slope_change=config.entry_on_slope_change,
            max_adverse_ma_return=config.max_adverse_hourly_ma_return,
        )
        self.levels = MaDeviationModel(
            config.ma_period,
            config.deviation_lookback,
            config.first_exit_std,
            config.final_exit_std,
        )
        self.entry_cross = (
            MaCrossModel(fast=config.cross_fast_period, slow=config.cross_slow_period)
            if config.entry_trigger == "ma_cross"
            else None
        )
        self._cross_signal = 0
        self.entry_levels = (
            self.levels
            if config.entry_ma_period == config.ma_period
            else MaDeviationModel(config.entry_ma_period, config.deviation_lookback)
        )
        self.ma99_exit_model = (
            MaDeviationModel(config.exit_ma_period, 1) if config.exit_on_ma99 else None
        )
        self.slope_risk = SlopeRiskModel(config.slope_risk_ema_period, config.slope_risk_scale)
        self._initialize_legs()
        self.mechanism_atr = HourlyATR()
        self.mechanism_snapshot = None
        self._initialize_execution()

    @staticmethod
    def _validate_config(config: MechanismTrendFollowingLong5MinConfig) -> None:
        if not isfinite(config.peak_profit_lock) or not 0 < config.peak_profit_lock < 1:
            raise ValueError("峰值浮盈锁定比例须位于0与1之间")
        if config.peak_profit_exit and (
            not config.native_stop_loss
            or config.max_loss_return is None
            or config.take_profit_activation_return is None
        ):
            raise ValueError("峰值止盈需要原生止损及正的止盈启用阈值")
        if config.exit_on_reverse_cross and config.entry_trigger != "ma_cross":
            raise ValueError("反向交叉退出要求交叉入场模式")
        if config.entry_trigger not in ("breakout", "ma_cross"):
            raise ValueError("未知入场触发方式")
        if config.max_positions_per_side < 1:
            raise ValueError("每方向最大份数必须为正")
        if not isfinite(config.additional_entry_spacing) or config.additional_entry_spacing <= 0:
            raise ValueError("新增仓位价格间距必须为有限正数")
        source_seconds_for(config.bar_type)
        if not isfinite(config.equity_fraction_per_side) or config.equity_fraction_per_side <= 0:
            raise ValueError("equity_fraction_per_side 必须为有限正数")
        if config.cooldown_bars < 0:
            raise ValueError("cooldown_bars 不能为负")
        if config.stop_loss_cooldown_seconds < 0:
            raise ValueError("止损冷静期不能为负")
        if config.max_loss_return is not None and (
            not isfinite(config.max_loss_return) or not 0 < config.max_loss_return < 1
        ):
            raise ValueError("持仓亏损阈值必须是 0 到 1 之间的有限数，或 None")
        if config.take_profit_activation_return is not None and (
            not isfinite(config.take_profit_activation_return)
            or config.take_profit_activation_return <= 0
        ):
            raise ValueError("止盈启动阈值必须为有限正数，或 None")
        if config.exit_on_ma_slope_reversal and config.tighten_exits_with_slope_risk:
            raise ValueError("斜率直接全平与风险收紧不能同时启用")
        if (
            not isfinite(config.slope_risk_max_tightening_std)
            or config.slope_risk_max_tightening_std < 0
        ):
            raise ValueError("风险最大收紧幅度必须为有限非负数")
        if config.slope_risk_rule not in ("fixed", "distance"):
            raise ValueError("未知风险保护线算法")
        if (
            not isfinite(config.slope_risk_price_buffer_std)
            or config.slope_risk_price_buffer_std < 0
        ):
            raise ValueError("价格缓冲必须为有限非负数")
        for value in (config.slope_risk_long_multiplier,):
            if not isfinite(value) or not 0 <= value <= 1:
                raise ValueError("多空风险倍率必须在 0 到 1 之间")
        if not isfinite(config.profit_relaxation_scale) or config.profit_relaxation_scale <= 0:
            raise ValueError("盈利放松尺度必须为有限正数")

    def _initialize_legs(self) -> None:
        self.legs = tuple(
            TrendLegState(1, slot=i) for i in range(self.config.max_positions_per_side)
        )
        self.leg = self.legs[0]

    def on_start(self) -> None:
        self.instrument = self.cache.instrument(self.config.instrument_id)
        if self.instrument is None:
            self.log.error(f"找不到交易合约：{self.config.instrument_id}")
            self.stop()
            return
        self.subscribe_bars(self.config.bar_type)

    def on_bar(self, bar: Bar) -> None:
        if bar.bar_type != self.config.bar_type or self.instrument is None:
            return
        if self.config.cycle_time_exit_hours > 0:
            for state in self.legs:
                if state.invested and bar.ts_event - state.last_entry_fill_ns >= self.config.cycle_time_exit_hours * 3600_000_000_000:
                    self.cancel_entry(state)
                    self.submit_exit(state, half=False)
        if self.config.peak_profit_exit:
            self._update_peak_exits(bar)
        high = bar.high.as_double()
        low = bar.low.as_double()
        close = bar.close.as_double()
        hourly = self.one_hour.update(bar.ts_event, high, low, close)
        if hourly is not None:
            self.hourly_trend.update(hourly.close)
            self.mechanism_atr.update(
                bar.ts_event + 1_000_000, hourly.high, hourly.low, hourly.close
            )
        completed = self.five_minute.update(bar.ts_event, high, low, close)
        if completed is None:
            return
        high, low, close = completed.high, completed.low, completed.close
        levels = self._update_levels(high, low, close)
        if levels is None:
            return
        self.slope_risk.update(levels.ma_slope, levels.std)
        if bar.ts_event < self.config.trade_start_ns:
            return
        for state in self.legs:
            self._refresh_leg(state, levels, close)

    def _update_peak_exits(self, bar: Bar) -> None:
        for state in self.legs:
            if not state.invested or bar.ts_event <= state.last_entry_fill_ns:
                continue
            # Only use a full source bar held after entry. Its new stop is not retroactive.
            state.update_profit_peak(
                bar.high.as_decimal() if state.direction > 0 else bar.low.as_decimal(),
                Decimal(str(self.mechanism_snapshot.activation_return)),
                Decimal(str(self.config.peak_profit_lock)),
                dynamic=self.config.dynamic_peak_profit_lock,
            )
            trigger = state.peak_stop_price
            if trigger is not None and state.direction * (bar.close.as_decimal() - trigger) <= 0:
                self.cancel_entry(state)
                self.submit_exit(state, half=False)
            else:
                self._sync_stop(state)

    def _update_levels(self, high: float, low: float, close: float) -> TrendLevels | None:
        if self.entry_cross is not None:
            self._cross_signal = self.entry_cross.update(close)
        levels = self.levels.update(high, low, close)
        entry = (
            levels
            if self.entry_levels is self.levels
            else self.entry_levels.update(high, low, close)
        )
        exit_levels = (
            self.ma99_exit_model.update(high, low, close)
            if self.ma99_exit_model is not None
            else None
        )
        if levels is None or entry is None:
            return None
        if self.ma99_exit_model is not None and exit_levels is None:
            return None
        if self.entry_levels is not self.levels:
            levels = replace(levels, long_entry=entry.long_entry, short_entry=entry.short_entry)
        if exit_levels is not None:
            levels = replace(
                levels,
                long_first_exit=exit_levels.ma,
                long_final_exit=exit_levels.ma,
                short_first_exit=exit_levels.ma,
                short_final_exit=exit_levels.ma,
            )
        return levels

    def _refresh_leg(self, state: TrendLegState, levels: TrendLevels, close: float) -> None:
        first = levels.long_first_exit if state.direction > 0 else levels.short_first_exit
        final = levels.long_final_exit if state.direction > 0 else levels.short_final_exit
        if state.invested:
            if self.config.peak_profit_exit:
                return
            if self.config.tighten_exits_with_slope_risk:
                risk = self.slope_risk.coefficient(state.direction)
                if self.config.profit_relaxes_slope_risk and state.average_entry_price is not None:
                    profit_return = state.direction * (close / float(state.average_entry_price) - 1)
                    risk *= profit_risk_multiplier(
                        profit_return, self.config.profit_relaxation_scale
                    )
                adjusted = tightened_level(
                    final if state.direction > 0 else first,
                    close,
                    levels.std,
                    state.direction,
                    risk,
                    rule=self.config.slope_risk_rule,
                    max_tightening_std=self.config.slope_risk_max_tightening_std,
                    price_buffer_std=self.config.slope_risk_price_buffer_std,
                    multiplier=(self.config.slope_risk_long_multiplier),
                )
                if state.direction > 0:
                    final = adjusted
                else:
                    first = adjusted
            state.ratchet_exits(Decimal(str(first)), Decimal(str(final)))
            action = evaluate_exit(
                state,
                close,
                reverse_cross_signal=(
                    self._cross_signal if self.config.exit_on_reverse_cross else None
                ),
                max_loss_return=(
                    None if self.config.native_stop_loss else self.mechanism_snapshot.stop_return
                ),
                take_profit_activation_return=self.mechanism_snapshot.activation_return,
                ma_slope=(levels.ma_slope if self.config.exit_on_ma_slope_reversal else None),
                full_exit_on_first=(self.config.long_full_exit_on_first),
            )
            if action is not None:
                self.cancel_entry(state)
                self.submit_exit(
                    state,
                    half=action == ExitAction.HALF,
                )
            return

        can_reenter = state.observe_reentry_zone(
            Decimal(str(close)),
            Decimal(str(levels.ma)),
            Decimal(str(levels.std)),
            Decimal(str(self.config.rearm_std_multiplier)),
        )
        hourly_allowed = self.hourly_trend.direction == state.direction
        if (
            not can_reenter
            or not hourly_allowed
            or state.stop_cooldown_active(self.clock.timestamp_ns())
        ):
            self.cancel_entry(state)
            return

        # 未成交时使用最新保护线；成交后才开始单向棘轮。
        state.first_exit_level = Decimal(str(first))
        state.final_exit_level = Decimal(str(final))
        if self.entry_cross is not None and self._cross_signal != state.direction:
            self.cancel_entry(state)
            return
        entry = (
            close
            if self.entry_cross is not None
            else levels.long_entry
            if state.direction > 0
            else levels.short_entry
        )
        entry_price = Decimal(str(entry))
        if not self._additional_entry_allowed(
            state, self.instrument.make_price(entry).as_decimal()
        ):
            self.cancel_entry(state)
            return
        snapshot = trend_snapshot(
            float(entry_price),
            self.mechanism_atr.value,
            self.config.mechanism_stop_atr,
            self.config.mechanism_activation_atr,
            self.config.mechanism_price_risk,
            self.config.equity_fraction_per_side,
        )
        if snapshot is None:
            return
        if snapshot.activation_return < self.config.cycle_min_reward_cost_ratio * 0.001:
            return
        self.mechanism_snapshot = snapshot
        quantity = equity_quantity(
            self._account_equity(),
            entry_price,
            Decimal(str(snapshot.notional_fraction)),
            self.instrument.size_increment.as_decimal(),
        )
        if self.entry_cross is not None:
            self.submit_entry_market(state, quantity)
        else:
            self.upsert_entry(state, quantity, entry_price)

    def _additional_entry_allowed(self, state: TrendLegState, price: Decimal) -> bool:
        siblings = self.legs
        gap = Decimal(str(self.config.additional_entry_spacing))
        for other in siblings:
            if other is state:
                continue
            # Reserve only one pending entry per direction, avoiding duplicate orders at one level.
            if self._cached_order(other.entry_order_id) is not None:
                return False
            if other.invested:
                entry = other.average_entry_price
                if entry is None or abs(price - entry) <= entry * gap:
                    return False
        return True

    def _account_equity(self) -> Decimal:
        if self.instrument is None:
            return Decimal(0)
        account = self.portfolio.account(venue=self.config.instrument_id.venue)
        if account is None:
            return Decimal(0)
        balance = account.balance_total(self.instrument.quote_currency)
        equity = balance.as_decimal() if balance is not None else Decimal(0)
        unrealized = self.portfolio.unrealized_pnl(self.config.instrument_id)
        if unrealized is not None and unrealized.currency == self.instrument.quote_currency:
            equity += unrealized.as_decimal()
        return max(Decimal(0), equity)

    def _floor_quantity(self, value: Decimal) -> Decimal:
        if self.instrument is None:
            return Decimal(0)
        increment = self.instrument.size_increment.as_decimal()
        return (value / increment).to_integral_value(rounding=ROUND_DOWN) * increment

    def on_stop(self) -> None:
        self._stopping = True
        self._cancel_own_orders()
        if self.config.close_positions_on_stop:
            for position in self.cache.positions_open(
                instrument_id=self.config.instrument_id,
                strategy_id=self.id,
            ):
                self.close_position(position)
        self.unsubscribe_bars(self.config.bar_type)

    def on_reset(self) -> None:
        self.instrument = None
        self.five_minute.reset()
        self.one_hour.reset()
        self.hourly_trend.reset()
        if self.entry_cross is not None:
            self.entry_cross.reset()
        self._cross_signal = 0
        if self.ma99_exit_model is not None:
            self.ma99_exit_model.reset()
        self.levels.reset()
        if self.entry_levels is not self.levels:
            self.entry_levels.reset()
        self.slope_risk.reset()
        self._initialize_legs()
        self.mechanism_atr = HourlyATR()
        self.mechanism_snapshot = None
        self._reset_execution()
