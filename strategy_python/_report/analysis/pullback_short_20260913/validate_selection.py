import json
import numpy as np
import pandas as pd
from research import ROOT,data,aggregate,simulate

selected=json.loads((ROOT/'selection.json').read_text());m=data()
b=aggregate(m,'1h',60);d=aggregate(m,'1D',1440)
ma=d.close.rolling(20).mean();std=d.close.rolling(20).std(ddof=0)
gate=((d.low<ma-2*std)&(d.close<ma)).reindex(b.index,method='ffill').fillna(False)
b['ma']=b.close.rolling(20).mean()
tr=pd.concat([b.high-b.low,(b.high-b.close.shift()).abs(),(b.low-b.close.shift()).abs()],axis=1).max(axis=1);tr[b.close.isna()|b.close.shift().isna()]=np.nan
b['stop']=b.high.rolling(12).max()+.25*tr.rolling(14).mean()
risk=b.stop/b.close-1
c=b.loc[gate&(b.close.shift()>=b.ma.shift())&(b.close<b.ma)&(risk>=.004)&(risk<=.03)]
results={}
for label,(start,end) in {'train':selected['development'],'validation':selected['validation'],'holdout':selected['holdout'],'full':['2021-01-01','2026-09-01']}.items():
 stats,trades=simulate(m,c,4,24,start,end);results[label]=stats;trades.to_csv(ROOT/f'selected_{label}_screen_trades.csv',index=False)
 print(label,stats,flush=True)
 print(trades.groupby(trades.exit.dt.year).pnl.sum(),flush=True)
(ROOT/'selected_screen_results.json').write_text(json.dumps(results,indent=2))
