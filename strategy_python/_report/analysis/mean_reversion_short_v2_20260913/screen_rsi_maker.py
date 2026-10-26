"""Same RSI study with 5bp market entry/exit, 2bp limit mean exit, plus 1bp per-side slippage. Native matching must verify actual liquidity flags."""
import itertools
import pandas as pd
from .research import ROOT,DATA,features,Market

m=pd.read_parquet(DATA);b=features(m);train=Market(m,'2021-01-01','2024-01-01');val=Market(m,'2024-01-01','2025-01-01');rows=[]
change=b.close.diff()
for rsiperiod in [2,5]:
 gains=change.clip(lower=0).ewm(alpha=1/rsiperiod,adjust=False).mean();loss=(-change.clip(upper=0)).ewm(alpha=1/rsiperiod,adjust=False).mean()
 rsi=100*gains/(gains+loss)
 for slowperiod,threshold,targetperiod,edge,stopatr,hours in itertools.product([99,200],[85,95],[5,10],[.002,.004],[2.,4.],[1,4]):
  slow=b.close.rolling(slowperiod).mean();target=b.close.rolling(targetperiod).mean()
  sig=(slow<slow.shift(12))&(b.close<slow)&(rsi>=threshold)&(b.close/target-1>=edge)
  stop=b.close+stopatr*b.atr;risk=stop/b.close-1
  c=b.loc[sig&(risk>=.004)&(risk<=.03)].assign(stop=stop,target=target)
  tr,_=train.simulate(c,hours,slip=.0001,maker_fee=.0002);va,_=val.simulate(c,hours,slip=.0001,maker_fee=.0002)
  rows.append(dict(rsiperiod=rsiperiod,slowperiod=slowperiod,threshold=threshold,targetperiod=targetperiod,edge=edge,stopatr=stopatr,hours=hours,**{'train_'+k:v for k,v in tr.items()},**{'val_'+k:v for k,v in va.items()}))
 print('completed',rsiperiod,flush=True)
f=pd.DataFrame(rows);f.to_csv(ROOT/'screen_rsi_maker.csv',index=False)
print(f.sort_values('train_pnl',ascending=False).head(20).to_string(index=False));print('POSITIVE BOTH');print(f.loc[(f.train_pnl>0)&(f.val_pnl>0)].sort_values('train_pnl',ascending=False).to_string(index=False))
