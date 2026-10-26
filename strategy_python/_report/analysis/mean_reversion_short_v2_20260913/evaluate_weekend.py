import json
import pandas as pd
from .research import ROOT,DATA,features,candidates,Market
s=json.loads((ROOT/'selection_weekend.json').read_text());p=s['parameters']
m=pd.read_parquet(DATA);b=features(m);c=candidates(b,'wick',p['boll_std'],p['min_mean_distance'],'down',p['stop_atr']);c=c.loc[c.index.dayofweek>=5]
results={}
for name,(start,end) in {'train':s['train'],'validation':s['validation'],'later':s['later'],'full':['2021-01-01','2026-09-01']}.items():
 stats,t=Market(m,start,end).simulate(c,p['max_holding_hours'],risk_fraction=.02,cap=3.,slip=.0001,maker_fee=.0002)
 results[name]=stats;t.to_csv(ROOT/f'weekend_{name}_screen_trades.csv',index=False)
 print(name,stats,flush=True);print(t.groupby(pd.to_datetime(t.exit_ns,utc=True).dt.year).pnl.sum(),flush=True)
(ROOT/'weekend_screen_results.json').write_text(json.dumps(results,indent=2)+'\n')
