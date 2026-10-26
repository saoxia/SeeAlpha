

from decimal import Decimal

import pytest

from nautilus_support import TestInstrumentProvider
from strategy_python.mean_reversion_short_5min import (
    MeanReversionShort5MinStrategy,
)
from strategy_python.mean_reversion_short_5min.position import EntryTarget
from tests.strategy_python.mean_reversion_short_5min.strategy_test import make_config


def test_execution_rejects_opposite_entry_target() -> None:
    strategy = MeanReversionShort5MinStrategy(make_config())
    with pytest.raises(ValueError, match="仅允许"):
        strategy.apply_entry_target(
            strategy.trade_state,
            EntryTarget(direction=1, role="initial", quantity=Decimal(1), price=Decimal(2000)),
        )


@pytest.mark.parametrize('units', ['.5', '1', '1.4', '2'])
@pytest.mark.parametrize('lower', ['95', '100', '105', '130'])
def test_two_take_profit_prices_cover_exact_remaining_position(units, lower):
    class ObserveTargets(MeanReversionShort5MinStrategy):
        def _upsert_exit(self, state, role, quantity, price, full_exit=False):
            calls[role] = (quantity, price, full_exit)

    calls = {}
    strategy = ObserveTargets(make_config())
    strategy.instrument = TestInstrumentProvider.ethusdt_perp_binance()
    state = strategy.trade_state
    state.quantity = Decimal(units)
    state.phase = 'scaled' if state.quantity > 1 else 'initial'
    state.unit_quantity = Decimal(1)
    state.position_id = 'TEST'
    state.entry_ma99 = Decimal(90)
    state.average_price = Decimal(100)
    state.last_entry_price = Decimal(100)
    strategy.features.stop_ma_distance.current_ma = 140.
    strategy.features.stop_p995 = 1.
    strategy.features.ma_distance.current_ma = 101.
    strategy.features.take_profit_distance_mean = -1.
    strategy.features.bollinger = (float(lower), 140.0, 160.0)
    strategy.features.min_bollinger_width = 0.
    strategy._refresh_protection(state)
    role = 'take_profit_second' if state.quantity > 1 else 'take_profit'
    assert calls[role] == (state.quantity, min(Decimal(lower), Decimal(100)), True)
    assert ('take_profit' if state.quantity > 1 else 'take_profit_second') not in calls
    assert calls['stop'][:2] == (state.quantity, Decimal(160))
    assert sum(qty for role, (qty, _, _) in calls.items() if role.startswith('take_profit')) == state.quantity
