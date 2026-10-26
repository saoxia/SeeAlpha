"""Five-minute rejection with partial return into bands, alternative mean windows.
Do not score the reserved period. Target must be below entry by >=0.2%.
"""
import itertools
import pandas as pd
from .research import ROOT,DATA,features,Market

m=pd.read_parquet(DATA);b=features(m);train=Market(m,'2021-01-01','2024-01-01');val=Market(m,'2024-01-01','2025-01-01');rows=[]
for period in [20,60,99]:
 b['ma']=b.close.rolling(period).mean();b['std']=b.close.rolling(period).std(ddof=0)
 for z,targetz,regime,stopatr,hours in itertools.product([2.,3.],[0.,1.],['all','down','flat'],[3.,6.],[4,12]):
  upper=b.ma+z*b['std'];stop=b.close+stopatr*b.atr;target=b.ma+targetz*b['std']
  gate=pd.Series(True,index=b.index) if regime=='all' else b[regime]
  sig=(b.high>=upper)&(b.close<b.open)&(b.close/target-1>=.003)&gate
  risk=stop/b.close-1
  c=b.loc[sig&(risk>=.004)&(risk<=.03)].assign(stop=stop,target=target)
  tr,_=train.simulate(c,hours);va,_=val.simulate(c,hours)
  rows.append(dict(period=period,z=z,targetz=targetz,regime=regime,stopatr=stopatr,hours=hours,**{'train_'+k:v for k,v in tr.items()},**{'val_'+k:v for k,v in va.items()}))
 print('completed',period,flush=True)
f=pd.DataFrame(rows);f.to_csv(ROOT/'screen_targets.csv',index=False)
print(f.sort_values('train_pnl',ascending=False).head(20).to_string(index=False));print('POSITIVE BOTH');print(f.loc[(f.train_pnl>0)&(f.val_pnl>0)].sort_values('train_pnl',ascending=False).to_string(index=False))
