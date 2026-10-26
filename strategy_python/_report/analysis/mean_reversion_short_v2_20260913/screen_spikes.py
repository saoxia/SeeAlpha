"""Abrupt 5m positive shock vs pre-shock volatility; mean-reversion short.
A genuinely different signal hypothesis, trained/validated on the same fixed split.
"""
import itertools
import pandas as pd
from .research import ROOT,DATA,features,Market

m=pd.read_parquet(DATA);b=features(m);train=Market(m,'2021-01-01','2024-01-01');val=Market(m,'2024-01-01','2025-01-01');rows=[]
for jump,strength,regime,stopfrac,returnfrac,hours in itertools.product([.008,.015],[2.,4.],['all','down'],[.01,.02,.03],[.5,1.],[.5,2,8]):
 gate=pd.Series(True,index=b.index) if regime=='all' else b[regime]
 sig=(b.close/b.open-1>=jump)&((b.close-b.open)>=strength*b.atr.shift())&gate
 target=b.close-returnfrac*(b.close-b.ma);stop=b.close*(1+stopfrac)
 c=b.loc[sig&(b.close/target-1>=.003)].assign(stop=stop,target=target)
 tr,_=train.simulate(c,hours);va,_=val.simulate(c,hours)
 rows.append(dict(jump=jump,strength=strength,regime=regime,stopfrac=stopfrac,returnfrac=returnfrac,hours=hours,**{'train_'+k:v for k,v in tr.items()},**{'val_'+k:v for k,v in va.items()}))
f=pd.DataFrame(rows);f.to_csv(ROOT/'screen_spikes.csv',index=False)
print(f.sort_values('train_pnl',ascending=False).head(20).to_string(index=False));print('POSITIVE BOTH');print(f.loc[(f.train_pnl>0)&(f.val_pnl>0)].sort_values('train_pnl',ascending=False).to_string(index=False))
