"""Independent batch recomputation of every native entry from completed OHLC."""
import json
import numpy as np
import pandas as pd
from research import ROOT,data,aggregate

m=data();b=aggregate(m,'1h',60);d=aggregate(m,'1D',1440)
ma=d.close.rolling(20).mean();std=d.close.rolling(20).std(ddof=0)
gate=((d.low<ma-2*std)&(d.close<ma)).reindex(b.index,method='ffill').fillna(False)
b['ma']=b.close.rolling(20).mean()
tr=pd.concat([b.high-b.low,(b.high-b.close.shift()).abs(),(b.low-b.close.shift()).abs()],axis=1).max(axis=1)
tr[b.close.isna()|b.close.shift().isna()]=np.nan
b['stop']=b.high.rolling(12).max()+.25*tr.rolling(14).mean()
risk=b.stop/b.close-1
allowed=gate&(b.close.shift()>=b.ma.shift())&(b.close<b.ma)&(risk>=.004)&(risk<=.03)
results={}
for label in ('full','holdout'):
 audit=json.loads((ROOT/f'native_{label}_audit.json').read_text())
 p=audit['report'];f=pd.read_csv(p+'/fills.csv');entries=f[f.side=='SELL'].copy()
 t=pd.DatetimeIndex(pd.to_datetime(entries.ts_last,utc=True))+pd.Timedelta(milliseconds=1)
 assert allowed.reindex(t).all()
 assert np.allclose(entries.avg_px,b.close.reindex(t).to_numpy(),rtol=0,atol=1e-8)
 results[label]={'native_entries_checked':len(entries),'all_use_completed_hour_and_day':True,
                 'all_entry_fills_at_signal_close_not_earlier_intrabar_price':True}
(ROOT/'signal_audit.json').write_text(json.dumps(results,indent=2)+'\n');print(results)
