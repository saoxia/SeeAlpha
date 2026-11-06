"""组合净额：相反目标不下对冲双单，单策略仍走原来的下单路径。"""

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from nautilus_trader.backtest import BacktestEngine
from nautilus_trader.config import BacktestEngineConfig, LoggerConfig
from nautilus_trader.model import (
    AccountType,
    Bar,
    BarType,
    InstrumentId,
    Money,
    OmsType,
    OrderSide,
    TimeInForce,
)

from nautilus_support import TestInstrumentProvider, instrument_fee_model
from strategy_python._config import StrategyConfig
from strategy_python._execution.portfolio_coordinator import (
    PortfolioCoordinator,
    PortfolioCoordinatorConfig,
)
from strategy_python._execution.target_netting import (
    MemberTarget,
    execution_delta,
    net_signed_quantity,
    quantize_toward_zero,
)
from strategy_python._strategy import Strategy
from strategy_python.portfolio import (
    create_strategies,
    parse_portfolio,
    uses_net_execution,
    with_net_execution,
)
from tests.strategy_python.portfolio_test import default_document

INSTRUMENT = TestInstrumentProvider.ethusdt_perp_binance()
BAR_TYPE = BarType.from_str(f"{INSTRUMENT.id}-1-MINUTE-LAST-EXTERNAL")
BASE_TS = 1_700_000_000_000_000_000


class ProbeConfig(StrategyConfig, frozen=True):
    instrument_id: InstrumentId
    bar_type: BarType


def _config(tag: str) -> ProbeConfig:
    return ProbeConfig(instrument_id=INSTRUMENT.id, bar_type=BAR_TYPE, order_id_tag=tag)


def _bars(rows) -> list[Bar]:
    bars = []
    for index, (open_, high, low, close) in enumerate(rows):
        ts = BASE_TS + (index + 1) * 60_000_000_000
        bars.append(
            Bar(
                BAR_TYPE,
                INSTRUMENT.make_price(open_),
                INSTRUMENT.make_price(high),
                INSTRUMENT.make_price(low),
                INSTRUMENT.make_price(close),
                INSTRUMENT.make_qty(Decimal(10)),
                ts,
                ts,
            ),
        )
    return bars


def _run(strategies, bars, oms):
    engine = BacktestEngine(BacktestEngineConfig(logging=LoggerConfig(bypass_logging=True)))
    try:
        engine.add_venue(
            INSTRUMENT.id.venue,
            oms,
            AccountType.MARGIN,
            [Money(100000, INSTRUMENT.quote_currency)],
            default_leverage=Decimal(5),
            fee_model=instrument_fee_model(INSTRUMENT),
        )
        engine.add_instrument(INSTRUMENT)
        engine.add_strategies(strategies)
        engine.add_data(bars)
        engine.run()
        return list(engine.cache.orders()), engine.portfolio.net_position(INSTRUMENT.id)
    finally:
        engine.dispose()


class Reporter(Strategy):
    def __init__(self, config) -> None:
        super().__init__(config)
        self.quantities = []
        self.index = 0

    def on_start(self) -> None:
        self.subscribe_bars(self.config.bar_type)

    def on_bar(self, bar) -> None:
        if self.index >= len(self.quantities):
            return
        item = self.quantities[self.index]
        self.index += 1
        quantity, risk_exit = item if isinstance(item, tuple) else (item, False)
        self._portfolio_coordinator.report_target(
            str(self.strategy_id), quantity, risk_exit=risk_exit,
        )


class OrderMember(Strategy):
    def __init__(self, config) -> None:
        super().__init__(config)
        self.place = lambda strategy: None
        self.fills: list[tuple[Decimal, int]] = []
        self._sent = False

    def on_start(self) -> None:
        self.subscribe_bars(self.config.bar_type)

    def on_bar(self, bar) -> None:
        if not self._sent:
            self._sent = True
            self.place(self)

    def on_order_filled(self, event) -> None:
        self.fills.append((event.last_qty.as_decimal(), event.ts_event))


class EntryThenStop(Strategy):
    def __init__(self, config) -> None:
        super().__init__(config)
        self.filled = Decimal(0)
        self.stop_ts = None
        self._sent = False
        self._stop_id = None

    def on_start(self) -> None:
        self.subscribe_bars(self.config.bar_type)

    def on_bar(self, bar) -> None:
        if self._sent:
            return
        self._sent = True
        self.submit_order(
            self.order_factory.market(
                self.config.instrument_id,
                OrderSide.BUY,
                INSTRUMENT.make_qty(Decimal(1)),
            ),
        )

    def on_order_filled(self, event) -> None:
        if self._stop_id is not None and event.client_order_id == self._stop_id:
            self.stop_ts = event.ts_event
            return
        self.filled += event.last_qty.as_decimal()
        if self.filled < 1 or self._stop_id is not None:
            return
        stop = self.order_factory.stop_market(
            self.config.instrument_id,
            OrderSide.SELL,
            INSTRUMENT.make_qty(self.filled),
            INSTRUMENT.make_price(Decimal(1800)),
            time_in_force=TimeInForce.GTC,
            reduce_only=True,
            tags=["hard-stop"],
        )
        self._stop_id = stop.client_order_id
        self.submit_order(stop)


def _coordinator() -> PortfolioCoordinator:
    return PortfolioCoordinator(
        PortfolioCoordinatorConfig(
            instrument_id=INSTRUMENT.id,
            bar_type=BAR_TYPE,
            order_id_tag="PORTFOLIO_NET",
            close_positions_on_stop=False,
        ),
    )


def _bind(*strategies):
    coordinator = _coordinator()
    for strategy in strategies:
        strategy.attach_portfolio_coordinator(coordinator)
    return [coordinator, *strategies]


def _reporter(tag: str, quantities) -> Reporter:
    strategy = Reporter(_config(tag))
    strategy.quantities = quantities
    return strategy


def _member(tag: str, place) -> OrderMember:
    strategy = OrderMember(_config(tag))
    strategy.place = place
    return strategy


def test_opposite_targets_net_to_zero_and_submit_nothing():
    long = _reporter("LONG", [Decimal(1)])
    short = _reporter("SHORT", [Decimal(-1)])
    orders, net = _run(_bind(long, short), _bars([(2000, 2000, 2000, 2000)]), OmsType.NETTING)
    assert orders == []
    assert net == 0


def test_unequal_targets_submit_only_the_net_buy():
    long = _reporter("LONG", [Decimal(2)])
    short = _reporter("SHORT", [Decimal("-0.5")])
    orders, net = _run(_bind(long, short), _bars([(2000, 2001, 1999, 2000)]), OmsType.NETTING)
    assert [(order.side, order.quantity.as_decimal()) for order in orders] == [
        (OrderSide.BUY, Decimal("1.5")),
    ]
    assert orders[0].tags == ["portfolio-net-delta"]
    assert net == Decimal("1.5")


def test_risk_exit_is_traded_when_nothing_offsets_it():
    long = _reporter("LONG", [Decimal(1), (Decimal(0), True)])
    short = _reporter("SHORT", [Decimal(0), Decimal(0)])
    bars = _bars([(2000, 2000, 2000, 2000), (2000, 2000, 2000, 2000)])
    orders, net = _run(_bind(long, short), bars, OmsType.NETTING)
    sells = [order for order in orders if order.side == OrderSide.SELL]
    assert len(sells) == 1
    assert sells[0].quantity.as_decimal() == 1
    assert net == 0


def test_flat_price_does_not_fire_a_resting_stop_until_the_next_touch():
    holder = EntryThenStop(_config("LONG"))
    idle = _member("SHORT", lambda strategy: None)
    bars = _bars(
        [
            (2000, 2010, 1990, 2000),
            (2000, 2010, 1990, 2000),
            (1900, 1900, 1700, 1750),
        ],
    )
    orders, net = _run(_bind(holder, idle), bars, OmsType.NETTING)
    assert holder.stop_ts == bars[2].ts_event
    sells = [order for order in orders if order.side == OrderSide.SELL]
    assert len(sells) == 1
    assert net == 0


def test_opposite_market_orders_cross_without_a_venue_pair():
    def buy(strategy):
        strategy.submit_order(
            strategy.order_factory.market(
                strategy.config.instrument_id, OrderSide.BUY, INSTRUMENT.make_qty(Decimal(1)),
            ),
        )

    def sell(strategy):
        strategy.submit_order(
            strategy.order_factory.market(
                strategy.config.instrument_id, OrderSide.SELL, INSTRUMENT.make_qty(Decimal(1)),
            ),
        )

    long = _member("LONG", buy)
    short = _member("SHORT", sell)
    orders, net = _run(
        _bind(long, short), _bars([(2000, 2010, 1990, 2000), (2000, 2000, 2000, 2000)]), OmsType.NETTING,
    )
    assert orders == []
    assert net == 0
    assert long.fills and short.fills


def test_residual_after_crossing_is_one_order():
    def buy(strategy):
        strategy.submit_order(
            strategy.order_factory.market(
                strategy.config.instrument_id, OrderSide.BUY, INSTRUMENT.make_qty(Decimal(2)),
            ),
        )

    def sell(strategy):
        strategy.submit_order(
            strategy.order_factory.market(
                strategy.config.instrument_id, OrderSide.SELL, INSTRUMENT.make_qty(Decimal("0.5")),
            ),
        )

    long = _member("LONG", buy)
    short = _member("SHORT", sell)
    orders, net = _run(_bind(long, short), _bars([(2000, 2000, 2000, 2000)]), OmsType.NETTING)
    assert [(order.side, order.quantity.as_decimal()) for order in orders] == [
        (OrderSide.BUY, Decimal("1.5")),
    ]
    assert net == Decimal("1.5")
    assert sum(qty for qty, _ts in long.fills) == 2
    assert sum(qty for qty, _ts in short.fills) == Decimal("0.5")


def test_limit_intent_waits_for_the_next_bar_and_then_nets():
    def buy(strategy):
        strategy.submit_order(
            strategy.order_factory.limit(
                instrument_id=strategy.config.instrument_id,
                order_side=OrderSide.BUY,
                quantity=INSTRUMENT.make_qty(Decimal(1)),
                price=INSTRUMENT.make_price(Decimal(1900)),
                time_in_force=TimeInForce.GTC,
            ),
        )

    def sell(strategy):
        strategy.submit_order(
            strategy.order_factory.limit(
                instrument_id=strategy.config.instrument_id,
                order_side=OrderSide.SELL,
                quantity=INSTRUMENT.make_qty(Decimal(1)),
                price=INSTRUMENT.make_price(Decimal(2100)),
                time_in_force=TimeInForce.GTC,
            ),
        )

    long = _member("LONG", buy)
    short = _member("SHORT", sell)
    bars = _bars([(2000, 2200, 1800, 2000), (2000, 2200, 1800, 2000)])
    orders, net = _run(_bind(long, short), bars, OmsType.NETTING)
    assert orders == []
    assert net == 0
    assert long.fills[0][1] == bars[1].ts_event
    assert short.fills[0][1] == bars[1].ts_event


def test_standalone_strategy_still_submits_its_own_order():
    def buy(strategy):
        strategy.submit_order(
            strategy.order_factory.market(
                strategy.config.instrument_id, OrderSide.BUY, INSTRUMENT.make_qty(Decimal(1)),
            ),
        )

    strategy = _member("ALONE", buy)
    orders, net = _run([strategy], _bars([(2000, 2000, 2000, 2000)]), OmsType.NETTING)
    assert len(orders) == 1
    assert orders[0].side == OrderSide.BUY
    assert orders[0].strategy_id == strategy.strategy_id
    assert orders[0].tags != ["portfolio-net-delta"]
    assert net == 1


def test_targets_sum_and_risk_exit_stays_inside_the_net():
    assert net_signed_quantity(
        [MemberTarget("long", Decimal(2)), MemberTarget("short", Decimal("-0.5"))],
    ) == Decimal("1.5")
    assert execution_delta(Decimal("1.5"), Decimal(0)) == Decimal("1.5")
    # 风险退出把成员打到零后，若另一成员的目标正好等于原账户持仓，差额为零。
    flattened = net_signed_quantity(
        [MemberTarget("long", Decimal(0), risk_exit=True), MemberTarget("short", Decimal(1))],
    )
    assert execution_delta(flattened, Decimal(1)) == 0
    assert quantize_toward_zero(Decimal("-0.0004"), Decimal("0.001")) == 0


def test_explicit_risk_exit_cannot_increase_exposure():
    coordinator = _coordinator()
    coordinator.report_target("A", Decimal(1))
    with pytest.raises(ValueError, match="风险退出"):
        coordinator.report_target("A", Decimal(2), risk_exit=True)
    coordinator.report_target("A", Decimal(0), risk_exit=True)
    coordinator.report_target("B", Decimal(-1))
    assert coordinator.net_quantity() == Decimal(-1)


def test_portfolio_assembly_nets_only_multiple_members():
    single = default_document()
    single["strategies"][0]["risk_weight"] = "1"
    single["strategies"][1]["enabled"] = False
    started = datetime(2026, 8, 1, tzinfo=UTC)
    strategies, _budget = create_strategies(started, parse_portfolio(single))
    assert len(strategies) == 1
    assert not uses_net_execution(strategies)

    paired, _budget = create_strategies(started)
    assert uses_net_execution(paired)
    assert with_net_execution(paired)[0] is paired[0]._portfolio_coordinator
    assert len(with_net_execution(paired)) == len(paired) + 1

    independent = default_document()
    independent["execution"] = "independent"
    plain, _budget = create_strategies(started, parse_portfolio(independent))
    assert not uses_net_execution(plain)
    with pytest.raises(ValueError, match="execution"):
        broken = default_document()
        broken["execution"] = "reweight"
        parse_portfolio(broken)
