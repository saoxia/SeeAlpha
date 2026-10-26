"""NautilusTrader 与 ETHUSDT v4 领域逻辑之间的编排层。"""

from decimal import ROUND_DOWN, Decimal
from math import isfinite

from nautilus_trader.model.data import Bar
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.trading.strategy import Strategy

from strategy_python._indicator.bars import source_seconds_for
from strategy_python._risk.shared_budget import SharedRiskBudget
from strategy_python.mean_reversion_short_5min.bars import (
    CompletedBar,
)
from strategy_python.mean_reversion_short_5min.config import MeanReversionShort5MinConfig
from strategy_python.mean_reversion_short_5min.execution import EthExecutionMixin
from strategy_python.mean_reversion_short_5min.features import EthFeatureModel
from strategy_python.mean_reversion_short_5min.position import (
    EntryTarget,
    first_take_profit_price,
    add_entry_price,
    fixed_stop_price,
    remainder_stop_price,
    take_profit_prices,
)
from strategy_python.mean_reversion_short_5min.risk import (
    DirectionalRiskModel,
    RecoveryModel,
    RiskParameters,
)
from strategy_python.mean_reversion_short_5min.signal import EthEntrySignalModel
from strategy_python.mean_reversion_short_5min.state import DirectionState


class MeanReversionShort5MinStrategy(EthExecutionMixin, Strategy):
    """编排行情特征、风险定仓、目标订单和执行事件。"""

    def __init__(self, config: MeanReversionShort5MinConfig) -> None:
        self._validate_config(config)
        super().__init__(config)

        parameters = RiskParameters(
            risk_fraction=config.cycle_risk_fraction, max_units=config.max_units,
            entry_fee_rate=config.entry_fee_rate, forced_exit_fee_rate=config.forced_exit_fee_rate,
            stop_cost_buffer=config.stop_cost_buffer,
        )
        self.risk_model = DirectionalRiskModel(parameters)
        self.recovery_model = RecoveryModel()
        self.instrument: Instrument | None = None
        self.trade_state = DirectionState()
        self.legs = (self.trade_state,)

        self.features = EthFeatureModel(config)
        self.signal_model = EthEntrySignalModel(config)
        self._initialize_execution()
        self.shared_risk_budget: SharedRiskBudget | None = None
        self.shared_risk_key: str | None = None

    def attach_shared_risk_budget(self, budget: SharedRiskBudget, key: str) -> None:
        """在启动前接入同账户预算；独立运行时无需接入。"""
        if self.is_running or self.shared_risk_budget is not None:
            raise ValueError("共享预算必须在启动前且只绑定一次")
        budget.register(key, self._committed_cycle_risk)
        self.shared_risk_budget = budget
        self.shared_risk_key = key

    @staticmethod
    def _validate_config(config: MeanReversionShort5MinConfig) -> None:
        source_seconds_for(config.bar_type)
        if not isfinite(config.entry_ma_distance_quantile) or not 0 <= config.entry_ma_distance_quantile <= 1:
            raise ValueError("MA99 距离分位比例必须在 0 到 1 之间")
        if not 1 <= config.max_units <= 2:
            raise ValueError("max_units 必须位于 1 到 2 份之间")
        if not isfinite(config.cycle_risk_fraction) or not 0 < config.cycle_risk_fraction <= 1:
            raise ValueError("周期风险比例必须位于 (0, 1]")
        if not isfinite(config.bollinger_std_multiplier) or config.bollinger_std_multiplier <= 0:
            raise ValueError("BOLL 倍数必须为有限正数")
        for fee in (config.entry_fee_rate, config.forced_exit_fee_rate):
            if not isfinite(fee) or not 0 <= fee < 1:
                raise ValueError("费率必须位于 [0, 1)")
        if not isfinite(config.stop_cost_buffer) or config.stop_cost_buffer < 0:
            raise ValueError("止损成本缓冲必须为有限非负数")
        for fraction in (config.time_exit_loss_step, config.time_exit_max_loss):
            if not isfinite(fraction) or not 0 <= fraction < 1:
                raise ValueError("超时退出亏损比例必须为[0, 1)范围内的有限数值")

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
        self._observe_market_price(bar.close.as_decimal(), bar.ts_event)
        for state in self.legs:
            if self._activate_breakeven_exit(state, bar.ts_event):
                self._refresh_protection(state)
        completed = self.features.aggregate(bar)

        if completed[5] is not None:
            self._on_five_minute_bar(completed[5])

        for state in self.legs:
            if self._certain_initial_fill_then_stop(state, bar):
                self._submit_emergency_exit(state, "certain_same_bar_stop")

    def _on_five_minute_bar(self, bar: CompletedBar) -> None:
        bands = self.features.on_five_minute_bar(bar)
        for state in self.legs:
            if self._no_lower_touch_stop_due(state, bar, bands):
                state.exit_started = True
                state.no_lower_touch_exit_active = True
                meta = self._order_meta.get(state.entry_order_id)
                if meta is not None:
                    meta.invalidated = True
                self._cancel_entry(state)
                self._refresh_protection(state)
                return
        if bands is None:
            return
        # 回测预热仅更新指标，不允许在用户选择的交易区间外下单。
        if self.config.trade_start_ns and bar.ts_end < self.config.trade_start_ns:
            return
        for state in self.legs:
            self.recovery_model.on_five_minute_bar(
                state, bar, self.features.ma_distance.current_ma
            )
            if state.invested:
                self._refresh_add_order(state, bar.ts_end)
                self._refresh_protection(state)
            else:
                self._refresh_initial_order(state, bar.ts_end)

    def _no_lower_touch_stop_due(self, state: DirectionState, bar: CompletedBar,
                                 bands: tuple[float, float, float] | None) -> bool:
        # Only complete candles entirely within this position cycle count.
        step = 300_000_000_000
        if not state.invested:
            state.no_lower_touch_count = 0
            state.no_lower_touch_last_ns = None
            return False
        if state.no_lower_touch_exit_active:
            return False
        previous = state.no_lower_touch_last_ns
        if previous is not None and bar.ts_end <= previous:
            return False
        state.no_lower_touch_last_ns = bar.ts_end
        if previous is not None and bar.ts_end - previous != step:
            state.no_lower_touch_count = 0
        ma99 = self.features.ma_distance.current_ma
        if (state.position_start_ns is None or bar.ts_end - step < state.position_start_ns
                or bands is None or ma99 is None
                or not all(isfinite(x) for x in (*bands, bar.low, ma99))):
            state.no_lower_touch_count = 0
            return False
        state.no_lower_touch_count = (0 if bar.low <= bands[0] or bar.low <= ma99
                                      else state.no_lower_touch_count + 1)
        return state.no_lower_touch_count >= 48

    def _breakeven_exit_due(self, state: DirectionState, now_ns: int) -> bool:
        return (state.invested and state.last_add_fill_ns is not None
                and now_ns - state.last_add_fill_ns > 24 * 60 * 60 * 1_000_000_000)

    def _activate_breakeven_exit(self, state: DirectionState, now_ns: int) -> bool:
        if not self._breakeven_exit_due(state, now_ns):
            return False
        day_ns = 24 * 60 * 60 * 1_000_000_000
        steps = max(0, (now_ns - state.last_add_fill_ns - day_ns) // day_ns)
        loss_fraction = min(Decimal(steps) * Decimal(str(self.config.time_exit_loss_step)),
                            Decimal(str(self.config.time_exit_max_loss)))
        if state.breakeven_exit_active and loss_fraction == state.time_exit_loss_fraction:
            return False
        state.breakeven_exit_active = True
        state.time_exit_loss_fraction = loss_fraction
        state.exit_started = True
        meta = self._order_meta.get(state.entry_order_id)
        if meta is not None:
            meta.invalidated = True
        self._cancel_entry(state)
        return True

    def _stop_target_price(self, state: DirectionState) -> Decimal | None:
        if state.phase == "remainder":
            return remainder_stop_price(
                state.average_price, self.features.bollinger,
                self.config.bollinger_std_multiplier,
            )
        return fixed_stop_price(state.average_price)

    def _take_profit_prices(self, state: DirectionState) -> tuple[Decimal, Decimal] | None:
        if state.no_lower_touch_exit_active:
            price = state.average_price * Decimal("1.01")
            if not price.is_finite() or price <= 0:
                return None
            if self.instrument is not None:
                tick = self.instrument.price_increment.as_decimal()
                price = (price / tick).to_integral_value(rounding=ROUND_DOWN) * tick
            return price, price
        if state.breakeven_exit_active:
            average = state.average_price
            price = average * (1 + state.time_exit_loss_fraction)
            return (price, price) if average.is_finite() and average > 0 else None
        if state.phase == "remainder":
            return take_profit_prices(self.features.bollinger, self.features.ma_distance.current_ma,
                                      self.features.take_profit_distance_mean)
        first = first_take_profit_price(
            state.last_entry_price, self.features.bollinger, self.features.min_bollinger_width,
        )
        if first is None:
            return None
        if state.phase == "initial":
            return first, first
        prices = take_profit_prices(self.features.bollinger, self.features.ma_distance.current_ma,
                                    self.features.take_profit_distance_mean)
        return (first, prices[1]) if prices is not None else None

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
        if state.reentry_waiting or not self.features.entry_touch_allowed(now_ns):
            self._cancel_entry(state)
            return
        if any(meta.invalidated for meta in self._order_meta.values()):
            return  # 上一轮入场单的撤销/迟到成交处理完，才允许新周期。
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
        )
        if signal is None or not self.features.entry_distance_allowed(signal.price):
            self._cancel_entry(state)
            return
        distance = self.features.add_distance_mean
        if distance is None or not isfinite(distance):
            self._cancel_entry(state)
            return
        second_price = add_entry_price(
            Decimal(str(signal.price)), self.features.bollinger,
            self.features.ma_distance.current_ma, Decimal(str(distance)),
        )
        if second_price is None:
            self._cancel_entry(state)
            return
        sizing = self.risk_model.size_initial_entry(
            signal.direction, signal.price, signal.timestamp_ns, self._account_equity(),
            self._size_increment(), second_entry_price=float(second_price),
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
                quantity=sizing["unit_quantity"],
                price=Decimal(str(signal.price)),
                risk_per_quantity=sizing["risk_per_quantity"],
                planned_risk=sizing["planned_risk"],
                risk_context=sizing["risk_context"],
            ),
        )

    def _refresh_add_order(self, state: DirectionState, now_ns: int) -> None:
        if (state.exit_started or state.emergency_exit_reason is not None or not state.invested
                or self._breakeven_exit_due(state, now_ns)
                or state.units >= self.config.max_units or self.config.max_units < 2):
            self._cancel_entry(state)
            return
        for order_id in (state.take_profit_order_id, state.second_take_profit_order_id):
            take_profit = self._cached_order(order_id)
            if take_profit is not None and take_profit.filled_qty.as_decimal() > 0:
                return  # 一份止盈在部分成交中，先完成这一份再补仓。
        current = self._cached_order(state.entry_order_id)
        meta = self._order_meta.get(state.entry_order_id)
        if current is not None and meta is not None and meta.role == "initial":
            return  # 首仓部分成交时先完成首仓，不替换为第二单。
        price = add_entry_price(state.average_price, self.features.bollinger,
                                self.features.ma_distance.current_ma, state.add_distance_snapshot)
        if price is None or price <= 0:
            self._cancel_entry(state)
            return
        quantity = self._floor_quantity(state.unit_quantity * self.config.max_units - state.quantity)
        if quantity <= 0:
            return
        if not self._entry_preserves_cycle_risk(state, quantity, price):
            self._cancel_entry(state)
            return
        self.apply_entry_target(state, EntryTarget(direction=-1, role="add", quantity=quantity, price=price))

    def _entry_preserves_cycle_risk(self, state: DirectionState, quantity: Decimal, price: Decimal) -> bool:
        return self.risk_model.entry_preserves_cycle_risk(
            state, quantity, price,
        )

    def _snapshot_add_distance(self, state: DirectionState) -> None:
        distance = self.features.add_distance_mean
        state.add_distance_snapshot = (
            Decimal(str(distance)) if distance is not None and isfinite(distance) else None
        )

    def _lock_entry_ma99(self, state: DirectionState) -> None:
        ma = self.features.stop_ma_distance.current_ma
        state.entry_ma99 = Decimal(str(ma)) if ma is not None and isfinite(ma) else None

    def on_stop(self) -> None:
        self.cancel_all_orders(self.config.instrument_id)
        if self.config.close_positions_on_stop:
            for position in self.cache.positions_open(
                instrument_id=self.config.instrument_id,
                strategy_id=self.id,
            ):
                self.close_position(position)
        self.unsubscribe_bars(self.config.bar_type)

    def on_reset(self) -> None:
        self.instrument = None
        self.trade_state = DirectionState()
        self.legs = (self.trade_state,)
        self.features.reset()
        self._reset_execution()
