"""Second hypothesis: slower pullbacks reduce microstructure churn.
Only development and validation dates, same cost assumptions as first screen.
"""
import itertools
import pandas as pd
import numpy as np
from research import data,aggregate,simulate,ROOT

m=data();base=aggregate(m,'4h',240);day=aggregate(m,'1D',1440)
hm=base.close.rolling(20).mean();hs=base.close.rolling(50).mean()
a=(base.close<hm)&(hm<hs)&(hm<hm.shift())
dm=day.close.rolling(20).mean();dd=(day.close<dm)&(dm<dm.shift(3))
rows=[]
for minutes in [15,60,240]:
 b=aggregate(m,f'{minutes}min',minutes)
 b['ma']=b.close.rolling(20).mean();b['std']=b.close.rolling(20).std(ddof=0)
 tr=pd.concat([b.high-b.low,(b.high-b.close.shift()).abs(),(b.low-b.close.shift()).abs()],axis=1).max(axis=1);tr[b.close.isna()|b.close.shift().isna()]=np.nan
 b['atr']=tr.rolling(14).mean();b['stop']=b.high.rolling(6).max()+.25*b.atr
 for regime,z,rr,hours in itertools.product(['4h','1d','both'],[.5,1.,2.],[1.5,2.5,4.],[24,72]):
  ga=a.reindex(b.index,method='ffill').fillna(False);gd=dd.reindex(b.index,method='ffill').fillna(False)
  gate=ga if regime=='4h' else gd if regime=='1d' else ga&gd
  reject=(b.high.shift()>=b.ma.shift()+z*b['std'].shift())&(b.close<b.close.shift())&(b.close<b.open)&(b.close>b.ma)
  risk=b.stop/b.close-1
  c=b.loc[gate&reject&(risk>=.004)&(risk<=.03)]
  tr,_=simulate(m,c,rr,hours,'2021-01-01','2024-01-01');va,_=simulate(m,c,rr,hours,'2024-01-01','2025-01-01')
  rows.append(dict(minutes=minutes,regime=regime,z=z,rr=rr,hours=hours,**{'train_'+k:v for k,v in tr.items()},**{'val_'+k:v for k,v in va.items()}))
 f=pd.DataFrame(rows);f.to_csv(ROOT/'screen_slow.csv',index=False)
f=pd.DataFrame(rows); print(f.sort_values('train_pnl',ascending=False).head(20).to_string(index=False));print('POSITIVE BOTH');print(f.loc[(f.train_pnl>0)&(f.val_pnl>0)].to_string(index=False))
