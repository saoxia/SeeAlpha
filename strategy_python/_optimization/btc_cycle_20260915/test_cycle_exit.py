from decimal import Decimal
from types import SimpleNamespace as NS

from strategy_python._optimization.btc_cycle_20260915.packages.mrl_original.strategy import MechanismMeanReversionLong5MinStrategy as Candidate

HOUR=3600_000_000_000

def owner(hours=24,band=0):
    state=NS(invested=True,position_start_ns=HOUR,direction=1,average_price=Decimal('100'),cycle_snapshot=NS(take_profit_return=.01))
    calls=[]
    obj=NS(legs=[state],_cycle_exiting=set(),config=NS(cycle_time_exit_hours=hours,cycle_band_stop_multiple=band),_cancel_state_orders=lambda s:calls.append(('cancel',s.direction)),_submit_emergency_exit=lambda s,r,role:calls.append((r,role)))
    return obj,state,calls

def test_time_exit_at_boundary_not_earlier():
    obj,state,calls=owner()
    Candidate._check_cycle_exits(obj,NS(ts_event=25*HOUR-1,close=Decimal('100')))
    assert calls==[]
    Candidate._check_cycle_exits(obj,NS(ts_event=25*HOUR,close=Decimal('100')))
    assert calls==[('cancel',1),('cycle_time_exit','cycle_exit')]
    assert obj._cycle_exiting=={1}

def test_structure_uses_frozen_gap_and_visible_close():
    obj,state,calls=owner(hours=0,band=4)
    Candidate._check_cycle_exits(obj,NS(ts_event=2*HOUR,close=Decimal('96.01')))
    assert calls==[]
    Candidate._check_cycle_exits(obj,NS(ts_event=2*HOUR+1,close=Decimal('96')))
    assert calls[-1]==('cycle_structure_exit','cycle_exit')

def test_disabled_rules_preserve_old_behavior_and_flat_resets_retry():
    obj,state,calls=owner(hours=0)
    Candidate._check_cycle_exits(obj,NS(ts_event=100*HOUR,close=Decimal('90')))
    assert calls==[]
    obj._cycle_exiting.add(1);state.invested=False
    Candidate._check_cycle_exits(obj,NS(ts_event=101*HOUR,close=Decimal('90')))
    assert obj._cycle_exiting==set()
