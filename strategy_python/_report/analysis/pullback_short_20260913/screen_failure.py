"""Third hypothesis: wait for MA support to fail after a real rebound.
All trials remain on development/validation; no holdout access for scoring.
"""
import itertools
import numpy as np
import pandas as pd
from research import data,aggregate,simulate,ROOT

m=data();h=aggregate(m,'4h',240);d=aggregate(m,'1D',1440)
hm=h.close.rolling(20).mean();hs=h.close.rolling(50).mean()
a=(h.close<hm)&(hm<hs)&(hm<hm.shift())
dm=d.close.rolling(20).mean();std=d.close.rolling(20).std(ddof=0)
down=(d.close<dm)&(dm<dm.shift(3));crisis=(d.low<dm-2*std)&(d.close<dm)
rows=[]
for minutes in [5,60]:
 b=aggregate(m,f'{minutes}min',minutes)
 b['ma']=b.close.rolling(20).mean();b['slow']=b.close.rolling(99).mean()
 tr=pd.concat([b.high-b.low,(b.high-b.close.shift()).abs(),(b.low-b.close.shift()).abs()],axis=1).max(axis=1)
 tr[b.close.isna()|b.close.shift().isna()]=np.nan
 b['atr']=tr.rolling(14).mean();b['stop']=b.high.rolling(12).max()+.25*b.atr
 for regime,trigger,rr,hours in itertools.product(['4h','both','crisis'],['ma20','ma99'],[1.5,2.5,4.,6.],[24,72]):
  ga=a.reindex(b.index,method='ffill').fillna(False)
  gd=down.reindex(b.index,method='ffill').fillna(False)
  gc=crisis.reindex(b.index,method='ffill').fillna(False)
  gate=ga if regime=='4h' else ga&gd if regime=='both' else gc
  ma=b.ma if trigger=='ma20' else b.slow
  reject=(b.close.shift()>=ma.shift())&(b.close<ma)
  risk=b.stop/b.close-1
  c=b.loc[gate&reject&(risk>=.004)&(risk<=.03)]
  tr,_=simulate(m,c,rr,hours,'2021-01-01','2024-01-01');va,_=simulate(m,c,rr,hours,'2024-01-01','2025-01-01')
  rows.append(dict(minutes=minutes,regime=regime,trigger=trigger,rr=rr,hours=hours,**{'train_'+k:v for k,v in tr.items()},**{'val_'+k:v for k,v in va.items()}))
 f=pd.DataFrame(rows);f.to_csv(ROOT/'screen_failure.csv',index=False)
f=pd.DataFrame(rows);print(f.sort_values('train_pnl',ascending=False).head(20).to_string(index=False));print('POSITIVE BOTH');print(f.loc[(f.train_pnl>0)&(f.val_pnl>0)].to_string(index=False))
