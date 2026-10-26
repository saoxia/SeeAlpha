"""Only quote after alternating band touches, dynamic mean reversion target.
Fixed price stops replace volatility-extrapolated stops. No averaging.
"""
import ctypes,itertools
import numpy as np
import pandas as pd
from .research import ROOT,DATA,features
from .screen_passive import arrays,library

m=pd.read_parquet(DATA);b=features(m)
up=b.high>=b.ma+2*b['std'];low=b.low<=b.ma-2*b['std']
idx=np.arange(len(b));lastup=np.maximum.accumulate(np.where(up,idx,-1));lastlow=np.maximum.accumulate(np.where(low,idx,-1))
b['rotation']=(lastlow>lastup)&(up.rolling(36).sum()>=2)&(low.rolling(36).sum()>=2)
b['rotation_down']=b.rotation&b.down
b['rotation_flat']=b.rotation&b.flat
lib=ctypes.CDLL(str(ROOT/'dynamic.so'));lib.run_dynamic.argtypes=library().run.argtypes;lib.run_dynamic.restype=None
rows=[]
for regime in ['rotation','rotation_down','rotation_flat']:
 train=arrays(m,b,regime,'2021-01-01','2024-01-01');val=arrays(m,b,regime,'2024-01-01','2025-01-01')
 for z,edge,stop,profit,hours in itertools.product([2.,3.],[.003,.005],[-.01,-.02,-.03],[.002,.004],[4,24]):
  pair=[]
  for a,ts in (train,val):
   out=np.zeros(4);lib.run_dynamic(a,ts,len(ts),z,edge,stop,profit,hours,60,.01,3.,out);pair.append(dict(zip(['pnl','n','pf','dd'],out)))
  tr,va=pair;rows.append(dict(regime=regime,z=z,edge=edge,stop=stop,profit=profit,hours=hours,**{'train_'+k:v for k,v in tr.items()},**{'val_'+k:v for k,v in va.items()}))
 print('completed',regime,flush=True)
f=pd.DataFrame(rows);f.to_csv(ROOT/'screen_rotation.csv',index=False)
print(f.sort_values('train_pnl',ascending=False).head(20).to_string(index=False));print('POSITIVE BOTH');print(f.loc[(f.train_pnl>0)&(f.val_pnl>0)].sort_values('train_pnl',ascending=False).to_string(index=False))
