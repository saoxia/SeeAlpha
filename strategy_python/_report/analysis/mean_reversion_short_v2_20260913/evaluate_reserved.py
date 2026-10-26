import json
import pandas as pd
from .research import ROOT,DATA,features,candidates,Market
selected=json.loads((ROOT/'selection.json').read_text());p=selected['parameters']
m=pd.read_parquet(DATA);b=features(m);c=candidates(b,p['entry_kind'],p['boll_std'],p['min_mean_distance'],p['regime'],p['stop_atr'])
results={}
for name,(start,end) in {'train':selected['train'],'validation':selected['validation'],'reserved':selected['reserved'],'full':['2021-01-01','2026-09-01']}.items():
 stats,t=Market(m,start,end).simulate(c,p['max_holding_hours'],risk_fraction=p['risk_fraction'],cap=3.)
 results[name]=stats;t.to_csv(ROOT/f'selected_{name}_screen_trades.csv',index=False)
 print(name,stats,flush=True)
 print(t.groupby(pd.to_datetime(t.exit_ns,utc=True).dt.year).pnl.sum(),flush=True)
(ROOT/'selected_screen_results.json').write_text(json.dumps(results,indent=2)+'\n')
