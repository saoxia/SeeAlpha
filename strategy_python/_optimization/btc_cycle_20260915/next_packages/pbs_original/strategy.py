"""行情聚合、信号、风险和订单执行之间的生命周期编排。"""

from decimal import Decimal
from math import isfinite

from nautilus_trader.trading.strategy import Strategy

from strategy_python._indicator.bars import CompletedBarAggregator, source_seconds_for
from strategy_python._mechanism.rules import (
    pullback_stop,
)
from strategy_python._optimization.btc_cycle_20260915.next_packages.pbs_original.config import (
    MechanismPullbackShort1hConfig,
)
from strategy_python._optimization.btc_cycle_20260915.next_packages.pbs_original.execution import (
    PullbackExecutionMixin,
)
from strategy_python._optimization.btc_cycle_20260915.next_packages.pbs_original.indicators import (
    PullbackIndicators,
)
from strategy_python._optimization.btc_cycle_20260915.next_packages.pbs_original.risk import (
    quantity_for_risk,
)
from strategy_python._optimization.btc_cycle_20260915.next_packages.pbs_original.signal import (
    short_setup,
)


class MechanismPullbackShort1hStrategy(PullbackExecutionMixin, Strategy):
    def __init__(self, config: MechanismPullbackShort1hConfig):
        for name in (
            "ma_period",
            "daily_boll_period",
            "atr_period",
            "swing_period",
            "max_holding_hours",
        ):
            value = getattr(config, name)
            if type(value) is not int or value <= 0:
                raise ValueError(f"{name} 必须为正整数")
        for name in (
            "daily_boll_std",
            "reward_risk",
            "risk_fraction",
            "max_notional_fraction",
            "min_stop_fraction",
            "max_stop_fraction",
            "stop_atr_buffer",
            "fee_buffer",
        ):
            value = getattr(config, name)
            if not isfinite(value) or value <= 0:
                raise ValueError(f"{name} 必须为有限正数")
        if not 0 < config.min_stop_fraction < config.max_stop_fraction < 1:
            raise ValueError("止损距离必须满足 0 < min < max < 1")
        if config.risk_fraction > 0.05 or config.max_notional_fraction > 5:
            raise ValueError("单笔风险不能超过5%，名义金额不能超过权益5倍")
        if config.max_holding_hours <= 0:
            raise ValueError("最长持仓时间必须为正")
        # Explicit historical replay: training-period overlap is declared in run metadata.
        super().__init__(config)
        seconds = source_seconds_for(config.bar_type)
        self.aggregators = [CompletedBarAggregator(n, source_seconds=seconds) for n in (1440, 60)]
        self.indicators = PullbackIndicators(
            ma_period=config.ma_period,
            daily_boll_period=config.daily_boll_period,
            daily_boll_std=config.daily_boll_std,
            atr_period=config.atr_period,
            swing_period=config.swing_period,
        )
        self.instrument = None
        self.last_price = Decimal(0)
        self.initialize_execution()

    def on_start(self):
        self.instrument = self.cache.instrument(self.config.instrument_id)
        if self.instrument is None:
            self.stop()
            return
        self.subscribe_bars(self.config.bar_type)

    def on_bar(self, bar):
        if bar.bar_type != self.config.bar_type or self.instrument is None:
            return
        self.last_price = bar.close.as_decimal()
        state = self.trade
        if state.quantity > 0:
            if state.exiting:
                self.exit_short("exit_retry")
            elif bar.ts_event - state.opened_ns >= self.config.max_holding_hours * 3600_000_000_000:
                self.exit_short("time_exit")
        values = (
            bar.ts_event,
            bar.open.as_double(),
            bar.high.as_double(),
            bar.low.as_double(),
            bar.close.as_double(),
        )
        daily, hourly = [aggregator.update(*values) for aggregator in self.aggregators]
        if daily is not None:
            self.indicators.update_daily(daily)
        if hourly is None:
            return
        features = self.indicators.update_hour(hourly)
        if (
            features is None
            or bar.ts_event < self.config.trade_start_ns
            or self.execution_busy()
            or bar.ts_event <= self.last_exit_ns
        ):
            return
        if self.config.cycle_require_falling_ma and features.ma >= features.previous_ma:
            return
        if not short_setup(features):
            return
        # [计算来源] 反弹结构高点+以本标的ATR计价的缓冲，随行情变化。
        # .25等缓冲倍数仍是经验输入，不是已经估计的噪声分位点。
        stop_value = pullback_stop(
            float(self.last_price),
            features.swing_high,
            features.atr,
            float(self.instrument.price_increment),
            self.config.mechanism_buffer_atr,
            self.config.mechanism_min_stop_atr,
            self.config.mechanism_max_stop_atr,
        )
        if stop_value is None:
            return
        stop = Decimal(str(stop_value))
        qty = quantity_for_risk(
            self.account_equity(),
            self.last_price,
            stop,
            risk_fraction=self.config.risk_fraction,
            max_notional_fraction=self.config.max_notional_fraction,
            fee_buffer=self.config.fee_buffer,
            increment=self.instrument.size_increment.as_decimal(),
        )
        self.enter_short(qty, stop)

    def account_equity(self):
        account = self.portfolio.account(venue=self.config.instrument_id.venue)
        if account is None:
            return Decimal(0)
        balance = account.balance_total(self.instrument.quote_currency)
        equity = balance.as_decimal() if balance is not None else Decimal(0)
        unrealized = self.portfolio.unrealized_pnl(self.config.instrument_id)
        if unrealized is not None:
            equity += unrealized.as_decimal()
        return max(Decimal(0), equity)

    def on_stop(self):
        self._stopping = True
        if self.config.close_positions_on_stop:
            self.exit_short("backtest_end")
        else:
            self.cancel_own_orders()
        self.unsubscribe_bars(self.config.bar_type)

    def on_reset(self):
        for aggregator in self.aggregators:
            aggregator.reset()
        self.indicators.reset()
        self.instrument = None
        self.last_price = Decimal(0)
        self.initialize_execution()
