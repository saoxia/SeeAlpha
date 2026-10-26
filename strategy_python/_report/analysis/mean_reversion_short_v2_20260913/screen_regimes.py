"""Five-minute range fading with anti-trend and daily weakness filters.
Training/validation only, same conservative passive fills and costs.
"""
import itertools
import numpy as np
import pandas as pd
from .research import ROOT,DATA,features,aggregate
from .screen_passive import arrays,library,sim

m=pd.read_parquet(DATA);b=features(m);d=aggregate(m,'1D',1440)
dm=d.close.rolling(20).mean();ds=d.close.rolling(20).std(ddof=0)
b['daily_down']=((d.close<dm)&(dm<dm.shift(3))).reindex(b.index,method='ffill').fillna(False)
b['crisis']=((d.low<dm-2*ds)&(d.close<dm)).reindex(b.index,method='ffill').fillna(False)
slow=b.close.rolling(99).mean()
b['slow_down']=(slow<slow.shift(12))&(b.close<slow)
b['combined']=b.slow_down&b.daily_down
b['range_down']=b.slow_down&b.flat
rows=[];lib=library()
for regime in ['daily_down','crisis','slow_down','combined','range_down']:
 train=arrays(m,b,regime,'2021-01-01','2024-01-01');val=arrays(m,b,regime,'2024-01-01','2025-01-01')
 for z,edge,stopatr,fraction,hours in itertools.product([1.5,2.,3.],[.003,.005],[2.,4.],[.5,1.],[2,8]):
  tr=sim(lib,*train,z,edge,stopatr,fraction,hours,60);va=sim(lib,*val,z,edge,stopatr,fraction,hours,60)
  rows.append(dict(regime=regime,z=z,edge=edge,stopatr=stopatr,fraction=fraction,hours=hours,cooldown=60,**{'train_'+k:v for k,v in tr.items()},**{'val_'+k:v for k,v in va.items()}))
 print('completed',regime,flush=True)
f=pd.DataFrame(rows);f.to_csv(ROOT/'screen_regimes.csv',index=False)
print(f.sort_values('train_pnl',ascending=False).head(20).to_string(index=False));print('POSITIVE BOTH');print(f.loc[(f.train_pnl>0)&(f.val_pnl>0)].sort_values('train_pnl',ascending=False).to_string(index=False))
