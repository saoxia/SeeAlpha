"""Verify unchanged defaults and new finite-lifetime behavior in the native engine."""
import hashlib
import json
from pathlib import Path

import pandas as pd

ROOT=Path(__file__).resolve().parent
PROJECT=ROOT.parents[2]

def main():
    cases=json.loads((ROOT/'smoke_cases.json').read_text())
    slots=json.loads((ROOT/'slots.json').read_text())
    output=[]
    for slot,period,variant in cases:
        path=ROOT/'runs'/period/(slot+('__'+variant if variant!='main' else ''))/'stream'
        m=json.loads((path/'metrics.json').read_text())
        assert abs(m['cashflow_reconciliation_error'])<1e-4
        assert m['off_tick_order_price_fields']==0
        fills=pd.read_csv(path/'fills.csv')
        if variant=='main':
            old=ROOT.parent/'mechanism_20260915/runs/stress'/slots[slot]/'stream'
            of=pd.read_csv(old/'fills.csv')
            columns=['ts_last','side','filled_qty','avg_px','commissions']
            pd.testing.assert_frame_equal(fills[columns],of[columns])
            om=json.loads((old/'metrics.json').read_text())
            for key in ['net_pnl','max_drawdown_pct','fees','cashflow_trades']:assert m[key]==om[key],(slot,key)
        if slot=='MRL_original' and variant=='time24':
            assert fills.tags.str.contains('cycle_time_exit').any(),'Time-exit path untested'
            times=pd.to_datetime(fills.ts_last,utc=True)
            hours=fills.assign(time=times).groupby('position_id').time.agg(lambda t:(t.max()-t.min()).total_seconds()/3600)
            assert hours.max()<=24+1/60+1e-6,hours.max()
        output.append({'slot':slot,'variant':variant,'trades':m['cashflow_trades'],'net_pnl':m['net_pnl']})
    prior=json.loads((ROOT/'prior_files_sha256.json').read_text())
    for path,digest in prior.items():assert hashlib.sha256((PROJECT/path).read_bytes()).hexdigest()==digest,path
    manifest={str(p.relative_to(PROJECT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in (ROOT/'packages').rglob('*.py')}
    for p in [ROOT/'run.py',ROOT/'factory.py',ROOT/'strategies.json']:
        manifest[str(p.relative_to(PROJECT))]=hashlib.sha256(p.read_bytes()).hexdigest()
    (ROOT/'frozen_runtime.json').write_text(json.dumps(manifest,indent=2)+'\n')
    (ROOT/'smoke_verification.json').write_text(json.dumps({'cases':output,'five_family_defaults_exact':True,'mrl_time_exit_exercised_and_bounded':True,'prior_files_unchanged':len(prior)},indent=2)+'\n')
    print('SMOKE_VERIFIED',len(output))

if __name__=='__main__':main()
