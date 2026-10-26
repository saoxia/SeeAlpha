from dataclasses import asdict
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace as NS

import pytest

from strategy_python._optimization.btc_cycle_20260915.factory import create_strategy as before
from strategy_python._optimization.btc_cycle_20260915.next_factory import create_strategy
from strategy_python._optimization.btc_cycle_20260915.next_packages.mrl_original.strategy import (
    MechanismMeanReversionLong5MinStrategy as Candidate,
)


@pytest.mark.parametrize('slot',['MRL_original','MRL_optimized'])
@pytest.mark.parametrize('units',[1,2,3,4])
def test_allocation_reserves_full_active_ladder_risk(slot,units):
    s=create_strategy('BTC',slot,datetime(2021,1,1,tzinfo=UTC),max_units=units)
    model=s.risk_model
    model.cycle_context=lambda *args:{'risk_fraction':.1}
    result=model.size_initial_entry(1,60000,1,Decimal(5000),Decimal('.001'))
    assert result is not None
    assert len(model.planned_entry_prices(1,60000))==units
    assert result['planned_risk']<=Decimal(500)
    assert (result['unit_quantity']+Decimal('.001'))*result['risk_per_quantity']>Decimal(500)


def test_existing_four_unit_default_parameters_unchanged():
    a=before('BTC','MRL_original',datetime(2021,1,1,tzinfo=UTC))
    b=create_strategy('BTC','MRL_original',datetime(2021,1,1,tzinfo=UTC))
    assert asdict(a.risk_model.parameters)==asdict(b.risk_model.parameters)
    with pytest.raises(ValueError):create_strategy('BTC','MRL_original',datetime(2021,1,1,tzinfo=UTC),max_units=5)


def test_rearm_blocks_new_quote_until_visible_middle_recovered():
    calls=[]
    state=NS(direction=1,invested=False)
    obj=NS(features=NS(on_five_minute_bar=lambda b:True,bollinger=(90,100,110)),legs=[state],_cycle_rearm={1},_cycle_exiting=set(),_cancel_entry=lambda s:calls.append('cancel'),_refresh_initial_order=lambda s,t:calls.append('enter'))
    Candidate._on_five_minute_bar(obj,NS(close=99,ts_end=1))
    assert calls==['cancel'] and obj._cycle_rearm=={1}
    Candidate._on_five_minute_bar(obj,NS(close=100,ts_end=2))
    assert calls==['cancel','enter'] and obj._cycle_rearm==set()


def test_fewer_reserved_levels_can_fit_minimum_lot_without_rounding_up():
    results = {}
    for units in (1, 4):
        strategy = create_strategy('BTC', 'MRL_optimized', datetime(2021, 1, 1, tzinfo=UTC), variant='flat_risk', max_units=units)
        model = strategy.risk_model
        fraction = model.parameters.risk_fraction
        model.cycle_context = lambda *args: {'risk_fraction': fraction}
        results[units] = model.size_initial_entry(1, 100000, 1, Decimal('421.39981'), Decimal('.001'))
        if results[units] is not None:
            assert results[units]['planned_risk'] <= Decimal('421.39981') * Decimal(str(fraction))
    assert results[4] is None
    assert results[1]['unit_quantity'] == Decimal('.002')
