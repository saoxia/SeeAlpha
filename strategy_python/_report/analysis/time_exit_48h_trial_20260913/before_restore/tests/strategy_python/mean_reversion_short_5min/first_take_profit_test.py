from decimal import Decimal

import pytest

from strategy_python.mean_reversion_short_5min import MeanReversionShort5MinStrategy
from tests.strategy_python.mean_reversion_short_5min.strategy_test import make_config


@pytest.mark.parametrize('phase', ['initial', 'scaled'])
@pytest.mark.parametrize('lower,width,expected', [(90., 5., 90), (98., 5., 95), (105., 10., 90), (105., 0., 100)])
def test_first_take_profit_uses_latest_entry_instead_of_average_minus_minimum_width(phase, lower, width, expected):
    strategy = MeanReversionShort5MinStrategy(make_config())
    f, state = strategy.features, strategy.trade_state
    state.phase, state.average_price = phase, Decimal(80)
    state.last_entry_price = Decimal(100)
    f.bollinger = (lower, 110., 120.)
    f.min_bollinger_width = width
    f.ma_distance.current_ma, f.take_profit_distance_mean = 100., -3.
    prices = strategy._take_profit_prices(state)
    assert prices[0] == Decimal(expected)
    assert prices[1] == (Decimal(expected) if phase == 'initial' else Decimal(str(min(lower, 97.))))
    state.average_price = Decimal(103)
    assert strategy._take_profit_prices(state)[0] == Decimal(expected)
    state.last_entry_price = Decimal(103)
    assert strategy._take_profit_prices(state)[0] == min(Decimal(str(lower)), Decimal(103)-Decimal(str(width)))


def test_missing_width_does_not_create_new_first_target_and_remainder_uses_original_second():
    strategy = MeanReversionShort5MinStrategy(make_config())
    f, state = strategy.features, strategy.trade_state
    state.average_price = Decimal(100)
    state.last_entry_price = Decimal(100)
    f.bollinger = (95., 100., 105.)
    f.ma_distance.current_ma, f.take_profit_distance_mean = 95., -5.
    assert strategy._take_profit_prices(state) is None
    state.phase = 'scaled'
    assert strategy._take_profit_prices(state) is None
    state.phase = 'remainder'
    assert strategy._take_profit_prices(state)[1] == Decimal(90)




@pytest.mark.parametrize('first,second,expected', [
    (95, 90, {'take_profit': ('1.5', 95, False), 'take_profit_second': ('.5', 90, False)}),
    (90, 95, {'take_profit_second': ('2', 90, True)}),
    (95, 95, {'take_profit_second': ('2', 95, True)}),
])
def test_comparing_prices_allocates_split_or_full_limit_order(first, second, expected):
    from nautilus_trader.test_kit.providers import TestInstrumentProvider

    calls = {}

    class Observe(MeanReversionShort5MinStrategy):
        def _upsert_exit(self, state, role, quantity, price, full_exit=False):
            if role != 'stop':
                calls[role] = (quantity, price, full_exit)

    strategy = Observe(make_config())
    strategy.instrument = TestInstrumentProvider.ethusdt_perp_binance()
    state = strategy.trade_state
    state.phase, state.position_id = 'scaled', 'test'
    state.quantity, state.unit_quantity, state.average_price = Decimal(2), Decimal(1), Decimal(100)
    strategy._take_profit_prices = lambda state: (Decimal(first), Decimal(second))
    strategy._refresh_protection(state)
    assert calls == {role: (Decimal(qty), Decimal(price), full) for role, (qty, price, full) in expected.items()}


def test_partial_full_exit_keeps_p1_limit_for_all_remaining_even_if_prices_reverse():
    from types import SimpleNamespace
    from nautilus_trader.model.objects import Money
    from nautilus_trader.test_kit.providers import TestInstrumentProvider
    from strategy_python.mean_reversion_short_5min.execution import OrderMeta

    instrument = TestInstrumentProvider.ethusdt_perp_binance()
    calls, adds = {}, []
    second = SimpleNamespace(is_closed=False, price=instrument.make_price(95))

    class Observe(MeanReversionShort5MinStrategy):
        def _strategy(self):
            return SimpleNamespace(cache=SimpleNamespace(order=lambda key: second if key == 'second' else None))

        def _cancel_entry(self, state):
            pass

        def _buy_market_price(self):
            return None

        def _upsert_exit(self, state, role, quantity, price, full_exit=False):
            calls[role] = (quantity, price, full_exit)

        def _submit_emergency_exit(self, state, reason):
            pytest.fail('Profit-taking must continue as a limit order')

        def _refresh_add_order(self, state, now):
            adds.append(True)

    strategy = Observe(make_config())
    strategy.instrument = instrument
    state = strategy.trade_state
    state.phase, state.position_id = 'scaled', 'test'
    state.quantity, state.unit_quantity, state.average_price = Decimal(2), Decimal(1), Decimal(100)
    state.second_take_profit_order_id = 'second'
    strategy._order_meta['second'] = OrderMeta(-1, 'take_profit_second', Decimal(2), full_exit=True)
    strategy._take_profit_prices = lambda state: (Decimal(96), Decimal(90))
    strategy.on_order_filled(SimpleNamespace(
        client_order_id='second', ts_event=1, position_id='test',
        last_qty=instrument.make_qty(.1), last_px=instrument.make_price(95),
        commission=Money(0, instrument.quote_currency)))
    assert state.exit_started and state.second_take_profit_full_exit
    assert calls['take_profit_second'] == (Decimal('1.9'), Decimal(96), True)
    assert 'take_profit' not in calls and not adds
    assert not state.reentry_waiting and not state.stop_exit_started
    state.clear_cycle()
    assert not state.second_take_profit_full_exit


def test_unfilled_full_order_can_switch_to_split_and_split_can_cancel_first_for_full():
    from types import SimpleNamespace
    from nautilus_trader.test_kit.providers import TestInstrumentProvider
    from strategy_python.mean_reversion_short_5min.execution import OrderMeta

    instrument = TestInstrumentProvider.ethusdt_perp_binance()
    calls, canceled = {}, []
    orders = {}

    def cancel(order):
        canceled.append(order.client_order_id)
        order.is_closed = True

    class Observe(MeanReversionShort5MinStrategy):
        def _strategy(self):
            return SimpleNamespace(cache=SimpleNamespace(order=lambda key: orders.get(key)), cancel_order=cancel)

        def _buy_market_price(self):
            return None

        def _upsert_exit(self, state, role, quantity, price, full_exit=False):
            calls[role] = (quantity, price, full_exit)

    strategy = Observe(make_config())
    strategy.instrument = instrument
    state = strategy.trade_state
    state.phase, state.position_id = 'scaled', 'test'
    state.quantity, state.unit_quantity, state.average_price = Decimal(2), Decimal(1), Decimal(100)
    state.second_take_profit_order_id = 'second'
    orders['second'] = SimpleNamespace(is_closed=False, quantity=instrument.make_qty(2),
                                       filled_qty=instrument.make_qty(0))
    strategy._order_meta['second'] = OrderMeta(-1, 'take_profit_second', Decimal(2), full_exit=True)
    strategy._take_profit_prices = lambda state: (Decimal(95), Decimal(90))
    strategy._refresh_protection(state)
    assert calls['take_profit'] == (Decimal('1.5'), Decimal(95), False)
    assert calls['take_profit_second'] == (Decimal('.5'), Decimal(90), False)
    state.take_profit_order_id = 'first'
    orders['first'] = SimpleNamespace(client_order_id='first', is_closed=False, is_pending_cancel=False)
    calls.clear()
    strategy._take_profit_prices = lambda state: (Decimal(90), Decimal(95))
    strategy._refresh_protection(state)
    assert canceled == ['first'] and 'take_profit' not in calls
    assert calls['take_profit_second'] == (Decimal(2), Decimal(90), True)


def test_latest_entry_price_updates_on_each_actual_entry_fill_and_survives_reductions():
    from strategy_python.mean_reversion_short_5min.state import DirectionState

    state = DirectionState()
    state.apply_entry(Decimal('.4'), Decimal(100), Decimal(0))
    assert state.last_entry_price == Decimal(100)
    state.apply_entry(Decimal('.6'), Decimal(102), Decimal(0))
    assert state.last_entry_price == Decimal(102)
    assert state.average_price == Decimal('101.2')
    state.apply_entry(Decimal(1), Decimal(110), Decimal(0))
    assert state.last_entry_price == Decimal(110) and state.average_price == Decimal('105.6')
    state.apply_exit(Decimal('1.5'))
    assert state.last_entry_price == Decimal(110)
    state.apply_entry(Decimal('.2'), Decimal(115), Decimal(0))
    assert state.last_entry_price == Decimal(115)
    state.clear_cycle()
    assert state.last_entry_price is None


def test_missing_latest_entry_price_does_not_fall_back_to_average():
    strategy = MeanReversionShort5MinStrategy(make_config())
    strategy.trade_state.average_price = Decimal(100)
    strategy.features.bollinger = (95., 100., 105.)
    strategy.features.min_bollinger_width = 3.
    assert strategy._take_profit_prices(strategy.trade_state) is None
