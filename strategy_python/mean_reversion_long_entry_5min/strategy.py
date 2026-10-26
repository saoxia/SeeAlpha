"""NautilusTrader 与 ETHUSDT v4 领域逻辑之间的编排层。"""
from collections import Counter
from dataclasses import replace
from decimal import ROUND_DOWN, Decimal
from math import isfinite

from nautilus_trader.model import Bar

from nautilus_support import Instrument, instrument_fee_rates, make_strategy_price
from strategy_python._indicator.bars import source_seconds_for
from strategy_python._mechanism.rules import (
    mean_reversion_snapshot,
)
from strategy_python._risk.shared_budget import SharedRiskBudget
from strategy_python._strategy import Strategy
from strategy_python.mean_reversion_long_entry_5min.admission import (
    ReclaimAdmission,
    reward_cost_allowed,
)
from strategy_python.mean_reversion_long_entry_5min.bars import (
    NANOSECONDS_PER_HOUR,
    CompletedBar,
)
from strategy_python.mean_reversion_long_entry_5min.config import (
    EntryMeanReversionLong5MinConfig,
)
from strategy_python.mean_reversion_long_entry_5min.execution import (
    EthExecutionMixin,
)
from strategy_python.mean_reversion_long_entry_5min.features import (
    EthFeatureModel,
)
from strategy_python.mean_reversion_long_entry_5min.position import (
    EntryTarget,
)
from strategy_python.mean_reversion_long_entry_5min.risk import (
    DirectionalRiskModel,
    RecoveryModel,
    RecoveryParameters,
    RiskParameters,
)
from strategy_python.mean_reversion_long_entry_5min.signal import (
    EthEntrySignalModel,
)
from strategy_python.mean_reversion_long_entry_5min.state import (
    DirectionState,
)


class EntryMeanReversionLong5MinStrategy(EthExecutionMixin, Strategy):
    """编排行情特征、风险定仓、目标订单和执行事件。"""

    def __init__(self, config: EntryMeanReversionLong5MinConfig) -> None:
        self._validate_config(config)
        # Explicit historical replay: training-period overlap is declared in run metadata.
        super().__init__(config)
        self._cycle_exiting = set()
        self._cycle_rearm = set()
        self.entry_admission = ReclaimAdmission()
        self.entry_quote_expires_ns = None
        self.entry_stats = Counter()

        parameters = RiskParameters(
            move_hours=config.risk_move_hours,
            reference_days=config.risk_reference_days,
            risk_fraction=config.cycle_risk_fraction,
            max_units=config.max_units,
            stop_distance=config.hard_stop_distance,
            entry_fee_rate=config.entry_fee_rate,
            forced_exit_fee_rate=config.forced_exit_fee_rate,
            stop_cost_buffer=config.stop_cost_buffer,
        )
        self.risk_model = DirectionalRiskModel(parameters)
        if config.mechanism_flat_risk:
            self.risk_model.parameters = replace(
                parameters,
                risk_fraction=parameters.risk_fraction * config.mechanism_flat_multiplier,
            )
            self.risk_model.strength_coefficient = lambda strength: 1.0
            self.risk_model.boundary_coefficient = lambda direction, position: 1.0
        self.recovery_model = RecoveryModel(
            RecoveryParameters(
                minimum_release_days=config.daily_risk_minimum_release_days,
                base_days=config.daily_risk_base_days,
                lower_slope=config.daily_risk_lower_slope,
                upper_slope=config.daily_risk_upper_slope,
                risk_coefficient=config.daily_risk_coefficient,
            ),
        )
        self.instrument: Instrument | None = None
        self.trade_state = DirectionState()
        self.legs = (self.trade_state,)

        self.features = EthFeatureModel(config)
        self.signal_model = EthEntrySignalModel(config)
        self._six_hour_history = self.features.six_hour_history
        self._initialize_execution()
        self.shared_risk_budget: SharedRiskBudget | None = None
        self.shared_risk_key: str | None = None

    def attach_shared_risk_budget(self, budget: SharedRiskBudget, key: str) -> None:
        """在启动前接入同账户预算；独立运行时无需接入。"""
        if self.is_running() or self.shared_risk_budget is not None:
            raise ValueError("共享预算必须在启动前且只绑定一次")
        budget.register(key, self._committed_cycle_risk)
        self.shared_risk_budget = budget
        self.shared_risk_key = key

    @staticmethod
    def _validate_config(config: EntryMeanReversionLong5MinConfig) -> None:
        source_seconds_for(config.bar_type)
        if (
            not isfinite(config.entry_min_reward_cost_ratio)
            or config.entry_min_reward_cost_ratio < 1
            or not isfinite(config.entry_cost_reserve_bp_per_side)
            or config.entry_cost_reserve_bp_per_side < 0
        ):
            raise ValueError("Entry cost ratio must be >=1 and reserve must be nonnegative")
        if config.entry_reclaim_filter and config.max_units != 1:
            raise ValueError("Reclaim research only supports a single entry unit")
        if config.max_units not in (1, 2, 3, 4):
            raise ValueError("Research allocation supports one to four existing ladder levels")
        if not 0 < config.ma_reentry_ratio <= 1:
            raise ValueError("ma_reentry_ratio 必须位于 (0, 1]")
        if not 0 <= config.entry_fee_rate < 1:
            raise ValueError("entry_fee_rate 必须位于 [0, 1)")
        if not 0 <= config.forced_exit_fee_rate < 1:
            raise ValueError("forced_exit_fee_rate 必须位于 [0, 1)")
        if config.stop_cost_buffer < 0:
            raise ValueError("stop_cost_buffer 不能为负")
        if config.daily_risk_lower_slope >= config.daily_risk_upper_slope:
            raise ValueError("日线风险区下界斜率必须小于上界斜率")

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
        self._check_cycle_exits(bar)
        completed = self.features.aggregate(bar)

        # 固定事件顺序来自迁移档案，改变顺序会改变策略语义。
        if completed[5] is not None:
            self._on_five_minute_bar(completed[5])
        if completed[15] is not None:
            self._on_fifteen_minute_bar(completed[15])
        if completed[360] is not None:
            self._on_six_hour_bar(completed[360])
        if completed[1440] is not None:
            self._on_daily_bar(completed[1440])
        if completed[60] is not None:
            self.risk_model.on_hourly_bar(completed[60])

        for state in self.legs:
            if self._certain_initial_fill_then_stop(state, bar):
                self._submit_emergency_exit(state, "certain_same_bar_stop")

    def _check_cycle_exits(self, bar):
        for state in self.legs:
            if not state.invested:
                self._cycle_exiting.discard(state.direction)
                continue
            if state.position_start_ns is None or bar.ts_event <= state.position_start_ns:
                continue
            reason = None
            if (
                self.config.cycle_time_exit_hours > 0
                and bar.ts_event - state.position_start_ns
                >= self.config.cycle_time_exit_hours * NANOSECONDS_PER_HOUR
            ):
                reason = "cycle_time_exit"
            snapshot = state.cycle_snapshot
            if (
                self.config.cycle_band_stop_multiple > 0
                and snapshot is not None
                and float(bar.close)
                <= float(state.average_price)
                * (1 - snapshot.take_profit_return * self.config.cycle_band_stop_multiple)
            ):
                reason = "cycle_structure_exit"
            if reason is not None or state.direction in self._cycle_exiting:
                self._cycle_exiting.add(state.direction)
                if self.config.cycle_rearm_after_exit:
                    self._cycle_rearm.add(state.direction)
                self._cancel_state_orders(state)
                self._submit_emergency_exit(state, reason or "cycle_exit_retry", role="cycle_exit")

    def _on_five_minute_bar(self, bar: CompletedBar) -> None:
        if self.config.entry_reclaim_filter:
            self.entry_admission.update(bar, self.features.bollinger)
        if self.features.on_five_minute_bar(bar) is None:
            return
        for state in self.legs:
            if state.direction in self._cycle_rearm:
                if (
                    not state.invested
                    and self.features.bollinger is not None
                    and bar.close >= self.features.bollinger[1]
                ):
                    self._cycle_rearm.discard(state.direction)
                else:
                    self._cancel_entry(state)
                    if not state.invested:
                        continue
            if state.direction in self._cycle_exiting:
                continue
            if state.invested:
                self._refresh_add_order(state, bar.ts_end)
                self._refresh_protection(state)
            else:
                self._refresh_initial_order(state, bar.ts_end)

    def _on_fifteen_minute_bar(self, bar: CompletedBar) -> None:
        self.features.on_fifteen_minute_bar(bar)

    def _on_six_hour_bar(self, bar: CompletedBar) -> None:
        ma52 = self.features.on_six_hour_bar(bar)
        if ma52 is None:
            return
        for state in self.legs:
            self.recovery_model.on_six_hour_bar(state, bar, ma52)

    def _on_daily_bar(self, bar: CompletedBar) -> None:
        ma52 = self.features.on_daily_bar(bar)
        if ma52 is not None:
            self.recovery_model.on_daily_bar(self.trade_state, bar, ma52)

    def _adverse_ma99_ratio(self, state: DirectionState) -> float | None:
        return self.features.adverse_ma99_ratio(state)

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

    def _size_increment(self) -> Decimal:
        if self.instrument is None:
            return Decimal(0)
        return self.instrument.size_increment.as_decimal()

    def _floor_quantity(self, value: Decimal) -> Decimal:
        increment = self._size_increment()
        if increment <= 0:
            return Decimal(0)
        return (value / increment).to_integral_value(rounding=ROUND_DOWN) * increment

    def _refresh_initial_order(self, state: DirectionState, now_ns: int) -> None:
        if self.shared_risk_budget is not None:
            # 等待撤单确认后才释放旧首仓额度，避免修改拒绝或在途单重复占用。
            self._cancel_entry(state)
            if (
                self._cached_order(state.entry_order_id) is not None
                or self._committed_cycle_risk() > 0
            ):
                return
        signal = self.signal_model.evaluate(
            state,
            now_ns,
            self.features.bollinger,
            self._adverse_ma99_ratio(state),
        )
        if signal is None:
            self._cancel_entry(state)
            return
        self.entry_stats["original_eligible"] += 1
        bands = self.features.bollinger
        if self.config.entry_reclaim_filter:
            quote = self.entry_admission.current(now_ns)
            if quote is None:
                self.entry_stats["reclaim_blocked"] += 1
                self._cancel_entry(state)
                return
            bands = quote.bands
            signal = replace(signal, price=bands[0])
            self.entry_quote_expires_ns = quote.expires_ns
        lower, middle, _ = bands
        snapshot = mean_reversion_snapshot(
            lower, middle, self.config.mechanism_add_multipliers, 2 * self.config.entry_fee_rate
        )
        if snapshot is None:
            self._cancel_entry(state)
            return
        if self.config.entry_cost_filter:
            entry = make_strategy_price(self.instrument, signal.price).as_decimal()
            target = make_strategy_price(self.instrument, entry * Decimal(str(1 + snapshot.take_profit_return))).as_decimal()
            fee = max(Decimal(0), instrument_fee_rates(self.instrument)[0], instrument_fee_rates(self.instrument)[1])
            if not reward_cost_allowed(
                entry,
                target,
                fee,
                self.config.entry_cost_reserve_bp_per_side,
                self.config.entry_min_reward_cost_ratio,
            ):
                self.entry_stats["cost_blocked"] += 1
                self._cancel_entry(state)
                return
        self.entry_stats["passed_admission"] += 1
        self.risk_model.parameters = replace(
            self.risk_model.parameters, add_offsets=snapshot.add_offsets
        )
        recovery = self.recovery_model.risk_coefficient(state, now_ns)
        sizing = self.risk_model.size_initial_entry(
            signal.direction,
            signal.price,
            signal.timestamp_ns,
            self._account_equity(),
            self._size_increment(),
            recovery,
        )
        if sizing is None:
            self._cancel_entry(state)
            return
        if self.shared_risk_budget is not None:
            available = self.shared_risk_budget.available(
                self.shared_risk_key,
                self._account_equity(),
            )
            quantity = self._floor_quantity(
                min(
                    sizing["unit_quantity"],
                    available / sizing["risk_per_quantity"],
                )
            )
            if quantity <= 0:
                return
            sizing["unit_quantity"] = quantity
            sizing["planned_risk"] = quantity * sizing["risk_per_quantity"]
        self.apply_entry_target(
            state,
            EntryTarget(
                direction=signal.direction,
                role="initial",
                snapshot=snapshot,
                quantity=sizing["unit_quantity"],
                price=Decimal(str(signal.price)),
                risk_per_quantity=sizing["risk_per_quantity"],
                planned_risk=sizing["planned_risk"],
                risk_context=sizing["risk_context"],
            ),
        )

    def _refresh_add_order(self, state: DirectionState, now_ns: int) -> None:
        if (
            state.position_start_ns is None
            or now_ns - state.position_start_ns
            >= self.config.max_holding_hours * NANOSECONDS_PER_HOUR
            or state.units >= self.config.max_units
        ):
            self._cancel_entry(state)
            return
        next_level = max(2, int(state.units) + 1)
        if next_level > self.config.max_units:
            self._cancel_entry(state)
            return
        target = state.unit_quantity * next_level
        quantity = self._floor_quantity(min(state.unit_quantity, target - state.quantity))
        if quantity <= 0:
            self._cancel_entry(state)
            return
        if state.cycle_snapshot is None:
            raise RuntimeError("Missing confirmed entry snapshot")
        self.risk_model.parameters = replace(
            self.risk_model.parameters, add_offsets=state.cycle_snapshot.add_offsets
        )
        offset = dict(state.cycle_snapshot.add_offsets)[next_level]
        price = state.average_price * Decimal(str(1 - state.direction * offset))
        if not self.risk_model.entry_preserves_cycle_risk(state, quantity, price):
            self._cancel_entry(state)
            return
        self.apply_entry_target(
            state,
            EntryTarget(
                direction=state.direction,
                role="add",
                snapshot=state.cycle_snapshot,
                quantity=quantity,
                price=price,
            ),
        )

    def on_stop(self) -> None:
        self.cancel_all_orders(self.config.instrument_id)
        if self.config.close_positions_on_stop:
            for position in self.cache.positions_open(
                instrument_id=self.config.instrument_id,
                strategy_id=self.strategy_id,
            ):
                self.close_position(position)
        self.unsubscribe_bars(self.config.bar_type)

    def on_reset(self) -> None:
        self.instrument = None
        self.trade_state = DirectionState()
        self.legs = (self.trade_state,)
        self.risk_model.hourly_bars.clear()
        self.risk_model.up_anchor = None
        self.risk_model.down_anchor = None
        self.risk_model._last_anchor_day = None
        self.features.reset()
        self.entry_admission.reset()
        self.entry_quote_expires_ns = None
        self.entry_stats.clear()
        self._cycle_exiting.clear()
        self._cycle_rearm.clear()
        self._reset_execution()
