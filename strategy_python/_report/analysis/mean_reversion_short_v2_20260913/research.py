"""Five-minute mean reversion screen. Fit 2021-23, select on 2024; reserve 2025+.
All signals use completed bars; entries at next minute open. Target is frozen
signal SMA20, stop based on ATR, no averaging and a fixed maximum holding time.
"""
import itertools
from pathlib import Path
import numpy as np
import pandas as pd

ROOT=Path(__file__).resolve().parent
DATA=ROOT.parent/'pullback_short_20260913'/'minutes.parquet'


def aggregate(m,freq,n):
    b=m.resample(freq,label='right',closed='right').agg({'open':'first','high':'max','low':'min','close':'last'})
    count=m.close.resample(freq,label='right',closed='right').count()
    b.loc[count!=n]=np.nan
    return b


def features(m):
    b=aggregate(m,'5min',5);h=aggregate(m,'1h',60)
    b['ma']=b.close.rolling(20).mean();b['std']=b.close.rolling(20).std(ddof=0)
    tr=pd.concat([b.high-b.low,(b.high-b.close.shift()).abs(),(b.low-b.close.shift()).abs()],axis=1).max(axis=1)
    tr[b.close.isna()|b.close.shift().isna()]=np.nan
    b['atr']=tr.rolling(14).mean()
    hm=h.close.rolling(20).mean()
    h['down']=(hm<=hm.shift(3))&(h.close<=hm)
    h['flat']=(h.close-h.close.shift(24)).abs()/h.close.diff().abs().rolling(24).sum()<.35
    for name in ('down','flat'):b[name]=h[name].reindex(b.index,method='ffill').fillna(False)
    return b


def candidates(b,kind,z,edge,regime,stop_atr):
    upper=b.ma+z*b['std']
    if kind=='reentry':sig=(b.close.shift()>upper.shift())&(b.close<upper)
    elif kind=='wick':sig=(b.high>=upper)&(b.close<b.open)
    elif kind=='exhaustion':sig=(b.high.shift()>=upper.shift())&(b.close<b.close.shift())&(b.close<b.open)
    else:raise ValueError(kind)
    sig &= b.close/b.ma-1>=edge
    if regime!='all':sig &= b[regime]
    stop=b.close+stop_atr*b.atr
    risk=stop/b.close-1
    sig &= (risk>=.004)&(risk<=.03)
    return b.loc[sig].assign(stop=stop,target=b.ma)


class Market:
    def __init__(self,m,start,end):
        self.start=pd.Timestamp(start,tz='UTC');self.end=pd.Timestamp(end,tz='UTC')
        m=m.loc[(m.index>self.start)&(m.index<=self.end)]
        self.times=m.index.asi8;self.o=m.open.to_numpy();self.h=m.high.to_numpy();self.l=m.low.to_numpy();self.c=m.close.to_numpy()

    def simulate(self,candidates,hours,risk_fraction=.01,cap=3.,fee=.0005,slip=.0002,maker_fee=None):
        c=candidates.loc[(candidates.index>=self.start)&(candidates.index<self.end)]
        times=self.times;balance=500.;free=0;records=[]
        for t,row in c.iterrows():
            i=np.searchsorted(times,t.value,side='right')
            if i<free or i>=len(times) or times[i]-t.value!=60_000_000_000:continue
            entry=self.o[i]*(1-slip);stop=row.stop;target=row.target;risk=stop-entry
            if risk<=0 or target>=entry:continue
            qty=np.floor(min(balance*risk_fraction/(risk+fee*(entry+stop)),balance*cap/entry)*1000)/1000
            if qty<=0:continue
            j=min(np.searchsorted(times,t.value+hours*3600_000_000_000),len(times)-1)
            hits=np.flatnonzero((self.h[i:j+1]>=stop)|(self.l[i:j+1]<=target))
            k=j;exit=self.c[j]*(1+slip);reason='time'
            if len(hits):
                k=i+hits[0]
                if self.h[k]>=stop:exit=max(stop,self.o[k])*(1+slip);reason='stop'
                else:exit=target*(1+slip);reason='mean'
            exit_fee = maker_fee if reason=="mean" and maker_fee is not None else fee
            pnl=qty*(entry-exit)-qty*(fee*entry+exit_fee*exit);balance+=pnl;free=k+1
            records.append((times[i],times[k],pnl,reason,balance,qty*entry/(balance-pnl)))
        r=pd.DataFrame(records,columns=['entry_ns','exit_ns','pnl','reason','balance','exposure'])
        if r.empty:return {'pnl':0.,'n':0,'pf':0.,'dd':0.},r
        eq=np.r_[500,r.balance];dd=np.min(eq/np.maximum.accumulate(eq)-1)
        return {'pnl':balance-500,'n':len(r),'pf':r.loc[r.pnl>0,'pnl'].sum()/-r.loc[r.pnl<0,'pnl'].sum(),'dd':dd},r


def main():
    m=pd.read_parquet(DATA);b=features(m);train=Market(m,'2021-01-01','2024-01-01');val=Market(m,'2024-01-01','2025-01-01');rows=[]
    for kind,z,edge,regime,stop_atr,hours in itertools.product(['reentry','wick'],[2.,3.],[.005,.01],['all','flat','down'],[2.,4.],[2,8]):
        c=candidates(b,kind,z,edge,regime,stop_atr)
        tr,_=train.simulate(c,hours);va,_=val.simulate(c,hours)
        rows.append(dict(kind=kind,z=z,edge=edge,regime=regime,stop_atr=stop_atr,hours=hours,**{'train_'+k:v for k,v in tr.items()},**{'val_'+k:v for k,v in va.items()}))
        if len(rows)%24==0:print('finished',len(rows),flush=True)
    f=pd.DataFrame(rows);f.to_csv(ROOT/'screen.csv',index=False)
    print(f.sort_values('train_pnl',ascending=False).head(20).to_string(index=False))
    print('POSITIVE BOTH');print(f.loc[(f.train_pnl>0)&(f.val_pnl>0)].sort_values('train_pnl',ascending=False).to_string(index=False))

if __name__=='__main__':main()
