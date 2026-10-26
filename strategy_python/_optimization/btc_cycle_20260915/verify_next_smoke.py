"""Validate next-cycle defaults, real active-layer caps, and finite time exits."""
import hashlib
import json
from pathlib import Path

import pandas as pd

ROOT=Path(__file__).resolve().parent
PROJECT=ROOT.parents[2]

def main():
    cases=json.loads((ROOT/'next_smoke_cases.json').read_text())
    records=[]
    for module,slot,period,variant in cases:
        p=ROOT/'runs'/period/(slot+'__'+variant)/'stream'
        f=pd.read_csv(p/'fills.csv');m=json.loads((p/'metrics.json').read_text())
        params=json.loads((p/'parameters.json').read_text())
        assert abs(m.get('cashflow_reconciliation_error',0))<1e-4 and m['off_tick_order_price_fields']==0
        if variant=='next_base':
            old=ROOT/'runs/stress'/slot/'stream';of=pd.read_csv(old/'fills.csv')
            cols=['ts_last','side','filled_qty','avg_px','commissions']
            pd.testing.assert_frame_equal(f[cols],of[cols])
            om=json.loads((old/'metrics.json').read_text())
            for key in ['net_pnl','fees','max_drawdown_pct','cashflow_trades']:assert m[key]==om[key],(slot,key)
        if variant in ['space_units1_same_risk','space_units2_same_risk']:
            cap=params['config']['max_units']
            assert cap in [1,2] and not f.empty
            for _,g in f.groupby('position_id'):
                g=g.sort_values('ts_last',kind='stable')
                unit=float(g[g.side=='BUY'].filled_qty.iloc[0])
                qty=g.filled_qty.astype(float).where(g.side=='BUY',-g.filled_qty.astype(float)).cumsum()
                assert qty.max()<=cap*unit+1e-9,(slot,qty.max(),unit)
        if variant=='next_time24_rearm':
            assert f.tags.str.contains('cycle_time_exit').any()
            t=pd.to_datetime(f.ts_last,utc=True)
            durations=f.assign(t=t).groupby('position_id').t.agg(lambda a:(a.max()-a.min()).total_seconds()/3600)
            assert durations.max()<=24+1/60+1e-6
        records.append({'slot':slot,'variant':variant,'trades':m['cashflow_trades'],'net_pnl':m['net_pnl'],'account_leverage':params['account_leverage']})
    for name in ['prior_files_sha256.json','frozen_runtime.json']:
        for p,d in json.loads((ROOT/name).read_text()).items():assert hashlib.sha256((PROJECT/p).read_bytes()).hexdigest()==d,p
    frozen={str(p.relative_to(PROJECT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in (ROOT/'next_packages').rglob('*.py')}
    for p in [ROOT/'next_run.py',ROOT/'next_factory.py',ROOT/'next_strategies.json']:
        frozen[str(p.relative_to(PROJECT))]=hashlib.sha256(p.read_bytes()).hexdigest()
    (ROOT/'frozen_next_runtime.json').write_text(json.dumps(frozen,indent=2)+'\n')
    (ROOT/'next_smoke_verification.json').write_text(json.dumps({'runs':records,'default_native_parity':True,'one_two_layer_caps_exercised':True,'time_exit_exercised':True},indent=2)+'\n')
    print('NEXT_SMOKE_VERIFIED',len(records))

if __name__=='__main__':main()
