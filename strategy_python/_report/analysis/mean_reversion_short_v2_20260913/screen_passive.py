"""Passive, precommitted five-minute bands; no future high used to set a quote."""
import ctypes
import itertools
import numpy as np
import pandas as pd
from .research import ROOT,DATA,features


def arrays(m,b,regime,start,end):
    q=b[['ma','std','atr','close']].copy();q['allowed']=True if regime=='all' else b[regime]
    x=q.reindex(m.index,method='ffill').shift(1)
    # The immediately following minute must be continuous with its predecessor.
    x.loc[m.index.to_series().diff()>pd.Timedelta(minutes=1),'allowed']=False
    a=pd.concat([m,x[['ma','std','atr','allowed','close']]],axis=1)
    a=a.loc[(a.index>pd.Timestamp(start,tz='UTC'))&(a.index<=pd.Timestamp(end,tz='UTC'))]
    return np.ascontiguousarray(a.to_numpy(dtype=float)),np.ascontiguousarray(a.index.asi8)


def library():
    lib=ctypes.CDLL(str(ROOT/'passive.so'));lib.run.argtypes=[np.ctypeslib.ndpointer(dtype=np.float64,flags='C_CONTIGUOUS'),np.ctypeslib.ndpointer(dtype=np.int64,flags='C_CONTIGUOUS'),ctypes.c_long]+[ctypes.c_double]*4+[ctypes.c_int]*2+[ctypes.c_double]*2+[np.ctypeslib.ndpointer(dtype=np.float64,flags='C_CONTIGUOUS')];lib.run.restype=None
    return lib


def sim(lib,a,ts,z,edge,stopatr,fraction,hours,cooldown,risk=.01,cap=3.):
    out=np.zeros(4);lib.run(a,ts,len(ts),z,edge,stopatr,fraction,hours,cooldown,risk,cap,out)
    return dict(zip(['pnl','n','pf','dd'],out))


def main():
    m=pd.read_parquet(DATA);b=features(m);lib=library();rows=[]
    for regime in ['all','flat','down']:
        train=arrays(m,b,regime,'2021-01-01','2024-01-01');val=arrays(m,b,regime,'2024-01-01','2025-01-01')
        for z,edge,stopatr,fraction,hours,cooldown in itertools.product([2.,3.],[.005,.01],[4.,8.],[.5,1.],[2,8],[60]):
            tr=sim(lib,*train,z,edge,stopatr,fraction,hours,cooldown);va=sim(lib,*val,z,edge,stopatr,fraction,hours,cooldown)
            rows.append(dict(regime=regime,z=z,edge=edge,stopatr=stopatr,fraction=fraction,hours=hours,cooldown=cooldown,**{'train_'+k:v for k,v in tr.items()},**{'val_'+k:v for k,v in va.items()}))
        print('completed',regime,flush=True)
    f=pd.DataFrame(rows);f.to_csv(ROOT/'screen_passive.csv',index=False)
    print(f.sort_values('train_pnl',ascending=False).head(20).to_string(index=False));print('POSITIVE BOTH');print(f.loc[(f.train_pnl>0)&(f.val_pnl>0)].sort_values('train_pnl',ascending=False).to_string(index=False))

if __name__=='__main__':main()
