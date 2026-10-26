"""趋势策略固定方向、实例隔离以及共享账户订单归属。"""
import importlib
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from nautilus_trader.backtest import BacktestEngine
from nautilus_trader.config import BacktestEngineConfig, LoggerConfig
from nautilus_trader.model import AccountType, Bar, BarType, Money, OmsType

from nautilus_support import TestInstrumentProvider, instrument_fee_model
from strategy_python.portfolio import create_strategies, parse_portfolio
from tests.strategy_python.portfolio_test import default_document


@pytest.mark.parametrize("side,direction", [("long", 1), ("short", -1)])
def test_only_matching_cross_and_hourly_filter_can_open(side, direction):
    module = importlib.import_module(f"strategy_python.trend_following_{side}_5min")
    levels_module = importlib.import_module(f"{module.__name__}.indicators")
    strategy_class = getattr(module, f"TrendFollowing{side.title()}5MinStrategy")
    config_class = getattr(module, f"TrendFollowing{side.title()}5MinConfig")
    instrument = TestInstrumentProvider.ethusdt_perp_binance()

    class Probe(strategy_class):
        def cancel_entry(self, state):
            pass

        def submit_entry_market(self, state, quantity):
            self.entries.append(state.direction)

        def _account_equity(self):
            return Decimal(500)

    strategy = Probe(
        config_class(
            instrument_id=instrument.id,
            bar_type=BarType.from_str(f"{instrument.id}-1-MINUTE-LAST-EXTERNAL"),
            **(
                {
                    "daily_boll_entry_filter": False,
                    "require_hourly_price_filter": True,
                    "require_hourly_slope_filter": True,
                }
                if side == "short"
                else {}
            ),
        )
    )
    engine = BacktestEngine(config=BacktestEngineConfig(logging=LoggerConfig(bypass_logging=True)))
    engine.add_strategy(strategy)
    strategy.instrument = instrument
    strategy.entries = []
    try:
        levels = levels_module.TrendLevels(2000, 10, 2020, 1980, 2002, 1997, 1998, 2003)
        for hourly, cross in [
            (-direction, -direction),
            (direction, -direction),
            (-direction, direction),
        ]:
            strategy.hourly_trend.direction = hourly
            strategy._cross_signal = cross
            strategy._refresh_leg(strategy.leg, levels, 2000)
        assert strategy.entries == []
        strategy.hourly_trend.direction = direction
        strategy._cross_signal = direction
        strategy._refresh_leg(strategy.leg, levels, 2000)
        assert strategy.entries == [direction]

    finally:
        engine.dispose()


def test_portfolio_creates_independent_trend_directions_and_parameters():
    value = default_document()
    value["risk"] = {"mode": "none"}
    value["strategies"] = [
        {
            "id": "TL",
            "strategy": "trend_following_long_5min",
            "parameters": {"peak_profit_lock": 0.7},
        },
        {
            "id": "TS",
            "strategy": "trend_following_short_5min",
            "parameters": {"peak_profit_lock": 0.6},
        },
    ]
    strategies, budget = create_strategies(datetime(2026, 8, 1, tzinfo=UTC), parse_portfolio(value))
    long, short = strategies
    assert budget is None
    assert long.strategy_id != short.strategy_id
    assert long.config.peak_profit_lock == 0.7 and short.config.peak_profit_lock == 0.6
    assert long.leg.direction == 1 and short.leg.direction == -1
    long.leg.record_entry_fill(Decimal(1), Decimal(2000))
    long.leg.stop_cooldown_until_ns = 123
    assert not short.leg.invested and short.leg.stop_cooldown_until_ns == 0
    long.on_reset()
    assert long.levels is not short.levels
    assert long._order_meta is not short._order_meta
    value["strategies"][0]["parameters"]["mode"] = "both"
    with pytest.raises(ValueError, match="未知参数"):
        create_strategies(datetime(2026, 8, 1, tzinfo=UTC), parse_portfolio(value))


def test_stopping_long_does_not_cancel_short_orders_or_close_short_positions():
    from strategy_python.trend_following_long_5min import (
        TrendFollowingLong5MinConfig,
        TrendFollowingLong5MinStrategy,
    )
    from strategy_python.trend_following_short_5min import (
        TrendFollowingShort5MinConfig,
        TrendFollowingShort5MinStrategy,
    )

    instrument = TestInstrumentProvider.ethusdt_perp_binance()
    bar_type = BarType.from_str(f"{instrument.id}-1-MINUTE-LAST-EXTERNAL")

    class LongProbe(TrendFollowingLong5MinStrategy):
        count = 0

        def on_bar(self, bar):
            self.count += 1
            if self.count == 1:
                self.submit_entry_market(self.leg, Decimal(1))
            elif self.count == 2:
                self.stop()

    class ShortProbe(TrendFollowingShort5MinStrategy):
        count = 0
        checked = False

        def on_bar(self, bar):
            self.count += 1
            if self.count == 1:
                self.submit_entry_market(self.leg, Decimal(1))
            elif self.count == 3:
                assert self.leg.invested
                assert self.cache.order(self.leg.stop_order_id).is_open
                assert len(self.cache.positions_open(strategy_id=self.strategy_id)) == 1
                self.checked = True
                self.submit_exit(self.leg, half=False)

    long = LongProbe(TrendFollowingLong5MinConfig(instrument_id=instrument.id, bar_type=bar_type))
    short = ShortProbe(
        TrendFollowingShort5MinConfig(instrument_id=instrument.id, bar_type=bar_type)
    )
    engine = BacktestEngine(config=BacktestEngineConfig(logging=LoggerConfig(bypass_logging=True)))
    try:
        engine.add_venue(
            venue=instrument.id.venue,
            oms_type=OmsType.HEDGING,
            account_type=AccountType.MARGIN,
            base_currency=None,
            starting_balances=[Money(2000, instrument.quote_currency)],
            default_leverage=Decimal(5),
            fee_model=instrument_fee_model(instrument),
        )
        engine.add_instrument(instrument)
        bars = []
        for minute in range(1, 5):
            timestamp = 1_780_000_000_000_000_000 + minute * 60_000_000_000
            price = instrument.make_price(2000)
            bars.append(
                Bar(
                    bar_type,
                    price,
                    price,
                    price,
                    price,
                    instrument.make_qty(100),
                    timestamp,
                    timestamp,
                )
            )
        engine.add_data(bars)
        engine.add_strategy(long)
        engine.add_strategy(short)
        engine.run()
        assert short.checked
        positions = engine.cache.positions()
        assert len(positions) == 2 and all(p.is_closed for p in positions)
        assert {p.entry.name for p in positions} == {"BUY", "SELL"}
        assert not engine.cache.orders_open()
        for strategy, side in [(long, "long"), (short, "short")]:
            entries = [
                o
                for o in engine.cache.orders(strategy_id=strategy.strategy_id)
                if o.tags and any(":entry:" in tag for tag in o.tags)
            ]
            assert len(entries) == 1
            assert entries[0].side.name == ("BUY" if side == "long" else "SELL")
            assert entries[0].tags[0].startswith(f"trend_following_{side}_5min:")
    finally:
        engine.dispose()
