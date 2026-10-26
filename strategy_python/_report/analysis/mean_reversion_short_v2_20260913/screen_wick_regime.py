"""Same 5m wick/mean target; verify persistent weak backgrounds instead of entry tinkering."""
import itertools
import pandas as pd
from .research import ROOT,DATA,features,candidates,Market,aggregate
m=pd.read_parquet(DATA);b=features(m);d=aggregate(m,'1D',1440)
dm=d.close.rolling(20).mean();ds=d.close.rolling(20).std(ddof=0)
b['daily_down']=((d.close<dm)&(dm<dm.shift(3))).reindex(b.index,method='ffill').fillna(False)
b['crisis']=((d.low<dm-2*ds)&(d.close<dm)).reindex(b.index,method='ffill').fillna(False)
b['both']=b.down&b.daily_down;b['both_crisis']=b.down&b.crisis
train=Market(m,'2021-01-01','2024-01-01');val=Market(m,'2024-01-01','2025-01-01');rows=[]
for regime,z,edge,stopatr,hours in itertools.product(['daily_down','crisis','both','both_crisis'],[2.5,3.],[.003,.005],[3.,4.,5.],[4,8]):
 c=candidates(b,'wick',z,edge,regime,stopatr)
 tr,_=train.simulate(c,hours);va,_=val.simulate(c,hours)
 rows.append(dict(regime=regime,z=z,edge=edge,stopatr=stopatr,hours=hours,**{'train_'+k:v for k,v in tr.items()},**{'val_'+k:v for k,v in va.items()}))
f=pd.DataFrame(rows);f.to_csv(ROOT/'screen_wick_regime.csv',index=False)
print(f.sort_values('train_pnl',ascending=False).head(20).to_string(index=False));print('POSITIVE BOTH');print(f.loc[(f.train_pnl>0)&(f.val_pnl>0)].sort_values('train_pnl',ascending=False).to_string(index=False))
