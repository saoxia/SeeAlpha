"""Test whether preserving partial reversion profits avoids loss round-trips.
Original reserved result was negative; never call it pristine after that look.
"""
import ctypes,itertools
import numpy as np
import pandas as pd
from .research import ROOT,DATA,features

lib=ctypes.CDLL(str(ROOT/'managed.so'));lib.run.argtypes=[np.ctypeslib.ndpointer(dtype=np.float64,flags='C_CONTIGUOUS'),np.ctypeslib.ndpointer(dtype=np.int64,flags='C_CONTIGUOUS'),ctypes.c_long]+[ctypes.c_double]*3+[ctypes.c_int]+[ctypes.c_double]*2+[np.ctypeslib.ndpointer(dtype=np.float64,flags='C_CONTIGUOUS')];lib.run.restype=None
m=pd.read_parquet(DATA);b=features(m);rows=[]
for regime,z in itertools.product(['all','flat','down'],[2.,3.]):
 sig=(b.high>=b.ma+z*b['std'])&(b.close<b.open)&(b.close/b.ma-1>=.005)
 if regime!='all':sig &= b[regime]
 x=b[['ma','atr']].assign(unused=0,signal=sig,previous=b.close).reindex(m.index,method='ffill').shift()
 x['signal'] &= m.index.minute%5==1
 x.loc[m.index.to_series().diff()>pd.Timedelta(minutes=1),'signal']=False
 a=pd.concat([m,x],axis=1)
 pairs=[]
 for start,end in [('2021-01-01','2024-01-01'),('2024-01-01','2025-01-01')]:
  f=a.loc[(a.index>pd.Timestamp(start,tz='UTC'))&(a.index<=pd.Timestamp(end,tz='UTC'))]
  pairs.append((np.ascontiguousarray(f.to_numpy(dtype=float)),np.ascontiguousarray(f.index.asi8)))
 for stopatr,activate,lock,hours in itertools.product([3.,4.,6.],[.003,.005,.01],[0.,.5,.75],[4,12]):
  result=[]
  for a,ts in pairs:
   out=np.zeros(4);lib.run(a,ts,len(ts),stopatr,activate,lock,hours,.01,3.,out);result.append(dict(zip(['pnl','n','pf','dd'],out)))
  tr,va=result;rows.append(dict(regime=regime,z=z,stopatr=stopatr,activate=activate,lock=lock,hours=hours,**{'train_'+k:v for k,v in tr.items()},**{'val_'+k:v for k,v in va.items()}))
 print('completed',regime,z,flush=True)
f=pd.DataFrame(rows);f.to_csv(ROOT/'screen_managed.csv',index=False)
print(f.sort_values('train_pnl',ascending=False).head(20).to_string(index=False));print('POSITIVE BOTH');print(f.loc[(f.train_pnl>0)&(f.val_pnl>0)].sort_values('train_pnl',ascending=False).to_string(index=False))
