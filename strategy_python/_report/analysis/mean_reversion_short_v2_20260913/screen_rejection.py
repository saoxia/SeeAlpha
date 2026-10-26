"""Failed five-minute range breakout: long upper wick, close back below prior high.
Target remains the 5m mean. Entry uses completed candle, never its earlier high.
"""
import itertools
import numpy as np
import pandas as pd
from .research import ROOT,DATA,features,Market

m=pd.read_parquet(DATA);b=features(m);train=Market(m,'2021-01-01','2024-01-01');val=Market(m,'2024-01-01','2025-01-01');rows=[]
for lookback,wick,regime,edge,hours in itertools.product([12,36],[.5,.7],['all','down','flat'],[.003,.005],[2,8]):
 previous_high=b.high.rolling(lookback).max().shift()
 rejection=(b.high>previous_high)&(b.close<previous_high)&(b.close<b.open)&((b.high-b.close)/(b.high-b.low)>=wick)
 gate=pd.Series(True,index=b.index) if regime=='all' else b[regime]
 stop=b.high+.25*b.atr;risk=stop/b.close-1
 c=b.loc[rejection&gate&(b.close/b.ma-1>=edge)&(risk>=.004)&(risk<=.03)].assign(stop=stop,target=b.ma)
 tr,_=train.simulate(c,hours);va,_=val.simulate(c,hours)
 rows.append(dict(lookback=lookback,wick=wick,regime=regime,edge=edge,hours=hours,**{'train_'+k:v for k,v in tr.items()},**{'val_'+k:v for k,v in va.items()}))
f=pd.DataFrame(rows);f.to_csv(ROOT/'screen_rejection.csv',index=False)
print(f.sort_values('train_pnl',ascending=False).head(20).to_string(index=False));print('POSITIVE BOTH');print(f.loc[(f.train_pnl>0)&(f.val_pnl>0)].sort_values('train_pnl',ascending=False).to_string(index=False))
