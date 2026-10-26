"""Exploratory screen; only 2021-2023 train and 2024 validation are scored.
2025-2026 is locked until selection.json has been written. Minute execution,
next-open entries, stop-first ambiguity, taker fees and adverse slippage.
"""
from pathlib import Path
import json
import itertools
import numpy as np
import pandas as pd
import pyarrow.parquet as pq

ROOT=Path(__file__).resolve().parent

def data():
    cache=ROOT/'minutes.parquet'
    if cache.exists(): return pd.read_parquet(cache)
    frames=[]
    for p in sorted(Path('data/catalog/data/bar/ETHUSDT-PERP.BINANCE-1-MINUTE-LAST-EXTERNAL').glob('*.parquet')):
        t=pq.read_table(p,columns=['ts_event','open','high','low','close'])
        d={'ts':t['ts_event'].to_numpy()}
        for col in ('open','high','low','close'):
            d[col]=np.array([int.from_bytes(x,'little',signed=True)/1e16 for x in t[col].to_pylist()])
        frames.append(pd.DataFrame(d))
    m=pd.concat(frames).sort_values('ts')
    if m.ts.duplicated().any(): raise ValueError('duplicate timestamps')
    m.index=pd.to_datetime(m.pop('ts')+1_000_000,utc=True)
    m=m.loc['2020-10-01':'2026-08-31']
    m.to_parquet(cache)
    gaps=m.index.to_series().diff(); print('DATA',len(m),'GAPS',gaps[gaps>pd.Timedelta(minutes=1)].to_dict(),flush=True)
    return m

def aggregate(m,freq,count):
    g=m.resample(freq,label='right',closed='right').agg({'open':'first','high':'max','low':'min','close':'last'})
    n=m.close.resample(freq,label='right',closed='right').count()
    g.loc[n!=count]=np.nan
    return g

def features(m):
    b=aggregate(m,'5min',5); h=aggregate(m,'4h',240); d=aggregate(m,'1D',1440)
    hm=h.close.rolling(20).mean(); hs=h.close.rolling(50).mean()
    a=(h.close<hm)&(hm<hs)&(hm<hm.shift(1))
    dm=d.close.rolling(20).mean(); z=(d.close<dm)&(dm<dm.shift(3))
    b['regime4h']=a.reindex(b.index,method='ffill').fillna(False)
    b['regime1d']=z.reindex(b.index,method='ffill').fillna(False)
    b['ma']=b.close.rolling(20).mean();b['std']=b.close.rolling(20).std(ddof=0)
    tr=pd.concat([b.high-b.low,(b.high-b.close.shift()).abs(),(b.low-b.close.shift()).abs()],axis=1).max(axis=1)
    tr[b.close.isna()|b.close.shift().isna()]=np.nan
    b['atr']=tr.rolling(14).mean();b['swing']=b.high.rolling(6).max()
    return b

def candidates(b,regime,z):
    gate=b.regime4h if regime=='4h' else b.regime1d if regime=='1d' else b.regime4h&b.regime1d
    reject=(b.high.shift()>=b.ma.shift()+z*b['std'].shift())&(b.close<b.close.shift())&(b.close<b.open)&(b.close>b.ma)
    stop=b.swing+0.25*b.atr
    risk=stop/b.close-1
    return b.loc[gate&reject&(risk>=0.004)&(risk<=0.03)].assign(stop=stop)

def simulate(m,c,rr,hours,start,end):
    minute=m.loc[start:end].iloc[:-1] if False else m.loc[(m.index>pd.Timestamp(start,tz='UTC'))&(m.index<=pd.Timestamp(end,tz='UTC'))]
    c=c.loc[(c.index>=pd.Timestamp(start,tz='UTC'))&(c.index<pd.Timestamp(end,tz='UTC'))]
    times=minute.index.asi8;o=minute.open.to_numpy();hi=minute.high.to_numpy();lo=minute.low.to_numpy();cl=minute.close.to_numpy()
    balance=500.;free=0;records=[];fee=.0005;slip=.0002
    for t,row in c.iterrows():
        i=np.searchsorted(times,t.value,side='right')
        if i<free or i>=len(times):continue
        # Missing next source minute invalidates entry.
        if times[i]-t.value!=60_000_000_000:continue
        entry=o[i]*(1-slip);stop=row.stop;risk=stop-entry
        if risk<=0 or risk/entry>.035:continue
        qty=np.floor(min(balance*.005/(risk+fee*(entry+stop)),balance/entry)*1000)/1000
        if qty<=0:continue
        target=entry-rr*risk
        j=min(np.searchsorted(times,t.value+hours*3600_000_000_000),len(times)-1)
        hits=np.flatnonzero((hi[i:j+1]>=stop)|(lo[i:j+1]<=target))
        reason='time';k=j;exit=cl[j]*(1+slip)
        if len(hits):
            k=i+hits[0]
            if hi[k]>=stop: exit=max(stop,o[k])*(1+slip);reason='stop'
            else: exit=target*(1+slip);reason='target'
        pnl=qty*(entry-exit)-qty*fee*(entry+exit);balance+=pnl;free=k+1
        records.append((pd.Timestamp(times[i],tz='UTC'),pd.Timestamp(times[k],tz='UTC'),pnl,reason,balance))
    r=pd.DataFrame(records,columns=['entry','exit','pnl','reason','balance'])
    if r.empty:return {'pnl':0,'n':0,'pf':0,'dd':0},r
    eq=np.r_[500,r.balance];dd=np.min(eq/np.maximum.accumulate(eq)-1)
    return {'pnl':balance-500,'n':len(r),'pf':r.loc[r.pnl>0,'pnl'].sum()/-r.loc[r.pnl<0,'pnl'].sum(),'dd':dd},r

def main():
    m=data();b=features(m);out=[]
    for regime,z,rr,hours in itertools.product(['4h','1d','both'],[1.,2.],[1.5,2.5,4.],[12,48]):
        c=candidates(b,regime,z)
        tr,_=simulate(m,c,rr,hours,'2021-01-01','2024-01-01');va,_=simulate(m,c,rr,hours,'2024-01-01','2025-01-01')
        row=dict(regime=regime,z=z,rr=rr,hours=hours,**{'train_'+k:v for k,v in tr.items()},**{'val_'+k:v for k,v in va.items()});out.append(row)
    f=pd.DataFrame(out);f.to_csv(ROOT/'screen.csv',index=False)
    print(f.sort_values('train_pnl',ascending=False).head(15).to_string(index=False),flush=True)
    print('POSITIVE BOTH',f.loc[(f.train_pnl>0)&(f.val_pnl>0)].to_string(index=False),flush=True)

if __name__=='__main__':main()
