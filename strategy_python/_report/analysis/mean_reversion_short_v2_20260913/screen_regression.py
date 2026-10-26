"""5m detrended mean reversion: regression-channel residual, not raw trend width.
Local 5m OLS slope <=0; sell positive residual and target fitted endpoint mean.
"""
import itertools
import numpy as np
import pandas as pd
from .research import ROOT,DATA,features,Market


def regression(close,period):
    # np.convolve reverses its kernel; weights correspond to oldest=0, latest=N-1.
    x=np.arange(period,dtype=float);center=(period-1)/2;sxx=((x-center)**2).sum()
    mean=close.rolling(period).mean();var=close.rolling(period).var(ddof=0)
    weighted=np.convolve(close.to_numpy(),(x-center)[::-1],mode='valid')
    slope=pd.Series(np.r_[np.full(period-1,np.nan),weighted/sxx],index=close.index)
    line=mean+slope*center
    std=np.sqrt((var-slope*slope*sxx/period).clip(lower=0))
    return line,std,slope


def main():
 m=pd.read_parquet(DATA);b=features(m);train=Market(m,'2021-01-01','2024-01-01');val=Market(m,'2024-01-01','2025-01-01');rows=[]
 for period in [36,72,144]:
  line,std,slope=regression(b.close,period)
  for z,edge,red,stopatr,hours in itertools.product([1.5,2.5],[.003,.005],[True,False],[2.,4.],[2,8]):
   sig=(slope<0)&(b.close>line+z*std)&(b.close/line-1>=edge)
   if red:sig &= b.close<b.open
   stop=b.close+stopatr*b.atr;risk=stop/b.close-1
   c=b.loc[sig&(risk>=.004)&(risk<=.03)].assign(stop=stop,target=line)
   tr,_=train.simulate(c,hours);va,_=val.simulate(c,hours)
   rows.append(dict(period=period,z=z,edge=edge,red=red,stopatr=stopatr,hours=hours,**{'train_'+k:v for k,v in tr.items()},**{'val_'+k:v for k,v in va.items()}))
  print('completed',period,flush=True)
 f=pd.DataFrame(rows);f.to_csv(ROOT/'screen_regression.csv',index=False)
 print(f.sort_values('train_pnl',ascending=False).head(20).to_string(index=False));print('POSITIVE BOTH');print(f.loc[(f.train_pnl>0)&(f.val_pnl>0)].sort_values('train_pnl',ascending=False).to_string(index=False))

if __name__=='__main__':main()
