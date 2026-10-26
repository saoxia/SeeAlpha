"""Coarse liquidity-session sensitivity, not hand-picked dates or years.
Train 2021-23 and validation 2024 only; rerun after applying entry-time filters.
"""
import itertools
import pandas as pd
from .research import ROOT,DATA,features,Market,candidates
from .screen_regression import regression

m=pd.read_parquet(DATA);b=features(m);train=Market(m,'2021-01-01','2024-01-01');val=Market(m,'2024-01-01','2025-01-01');rows=[]
line,std,slope=regression(b.close,36)
regsig=(slope<0)&(b.close>line+2.5*std)&(b.close<b.open)&(b.close/line-1>=.003)
stop=b.close+4*b.atr;risk=stop/b.close-1
models={'boll3':(candidates(b,'wick',3.,.005,'down',4.),12),
        'boll2':(candidates(b,'wick',2.,.005,'down',4.),12),
        'regression':(b.loc[regsig&(risk>=.004)&(risk<=.03)].assign(stop=stop,target=line),2)}
for name,(c,hours) in models.items():
 for session,days in itertools.product(['all','asia','europe','america'],['all','weekdays','weekend']):
  f=c
  if session!='all':
   begin={'asia':0,'europe':8,'america':16}[session];f=f.loc[(f.index.hour>=begin)&(f.index.hour<begin+8)]
  if days!='all':f=f.loc[(f.index.dayofweek>=5) if days=='weekend' else (f.index.dayofweek<5)]
  tr,_=train.simulate(f,hours,slip=.0001,maker_fee=.0002);va,_=val.simulate(f,hours,slip=.0001,maker_fee=.0002)
  rows.append(dict(model=name,session=session,days=days,hours=hours,**{'train_'+k:v for k,v in tr.items()},**{'val_'+k:v for k,v in va.items()}))
f=pd.DataFrame(rows);f.to_csv(ROOT/'screen_sessions.csv',index=False)
print(f.sort_values('train_pnl',ascending=False).head(20).to_string(index=False));print('POSITIVE BOTH');print(f.loc[(f.train_pnl>0)&(f.val_pnl>0)].sort_values('train_pnl',ascending=False).to_string(index=False))
