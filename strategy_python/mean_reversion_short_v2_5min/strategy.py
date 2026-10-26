"""五分钟均值回归做空的编排：指标→信号→风险定仓→订单。"""

from datetime import UTC, datetime
from decimal import Decimal
from math import isfinite

from strategy_python._indicator.bars import CompletedBarAggregator, source_seconds_for
from strategy_python._strategy import Strategy
from strategy_python.mean_reversion_short_v2_5min.config import MeanReversionShortV2Config
from strategy_python.mean_reversion_short_v2_5min.execution import MeanReversionExecutionMixin
from strategy_python.mean_reversion_short_v2_5min.indicators import MeanReversionIndicators
from strategy_python.mean_reversion_short_v2_5min.risk import quantity_for_risk
from strategy_python.mean_reversion_short_v2_5min.signal import mean_reversion_setup


class MeanReversionShortV2Strategy(MeanReversionExecutionMixin, Strategy):
    def __init__(self, config: MeanReversionShortV2Config):
        for name in ("boll_period", "atr_period", "max_holding_hours"):
            if type(getattr(config, name)) is not int or getattr(config, name) <= 0:
                raise ValueError(f"{name} 必须为正整数")
        for name in (
            "boll_std",
            "min_mean_distance",
            "stop_atr",
            "min_stop_fraction",
            "max_stop_fraction",
            "risk_fraction",
            "max_notional_fraction",
            "fee_buffer",
        ):
            if not isfinite(getattr(config, name)) or getattr(config, name) <= 0:
                raise ValueError(f"{name} 必须为有限正数")
        if config.entry_kind not in ("reentry", "wick") or config.regime not in (
            "all",
            "flat",
            "down",
        ):
            raise ValueError("入场模式或市场背景过滤无效")
        if not 0 < config.min_stop_fraction < config.max_stop_fraction < 1:
            raise ValueError("止损比例须满足 0 < min < max < 1")
        if config.max_notional_fraction > 5 or config.risk_fraction > 0.05:
            raise ValueError("名义杠杆上限5倍，单笔计划风险上限5%")
        super().__init__(config)
        seconds = source_seconds_for(config.bar_type)
        self.aggregators = [CompletedBarAggregator(n, source_seconds=seconds) for n in (60, 5)]
        self.indicators = MeanReversionIndicators(
            config.boll_period, config.boll_std, config.atr_period
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
        hourly, five = [a.update(*values) for a in self.aggregators]
        if hourly is not None:
            self.indicators.update_hour(hourly)
        if five is None:
            return
        f = self.indicators.update_five(five)
        if (
            f is None
            or bar.ts_event < self.config.trade_start_ns
            or self.execution_busy()
            or bar.ts_event <= self.last_exit_ns
        ):
            return
        if not mean_reversion_setup(
            f,
            entry_kind=self.config.entry_kind,
            regime=self.config.regime,
            boll_std=self.config.boll_std,
            min_mean_distance=self.config.min_mean_distance,
            weekend_only=self.config.weekend_only,
            weekday=datetime.fromtimestamp(five.ts_end / 1e9, tz=UTC).weekday(),
        ):
            return
        stop = self.last_price + Decimal(str(self.config.stop_atr * f.atr))
        if (
            not self.config.min_stop_fraction
            <= float(stop / self.last_price - 1)
            <= self.config.max_stop_fraction
        ):
            return
        qty = quantity_for_risk(
            self.account_equity(),
            self.last_price,
            stop,
            risk_fraction=self.config.risk_fraction,
            max_notional_fraction=self.config.max_notional_fraction,
            fee_buffer=self.config.fee_buffer,
            increment=self.instrument.size_increment.as_decimal(),
        )
        self.enter_short(qty, stop, Decimal(str(f.ma)))

    def account_equity(self):
        account = self.portfolio.account(venue=self.config.instrument_id.venue)
        if account is None:
            return Decimal(0)
        balance = account.balance_total(self.instrument.quote_currency)
        equity = balance.as_decimal() if balance else Decimal(0)
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
        for a in self.aggregators:
            a.reset()
        self.indicators.reset()
        self.instrument = None
        self.last_price = Decimal(0)
        self.initialize_execution()
