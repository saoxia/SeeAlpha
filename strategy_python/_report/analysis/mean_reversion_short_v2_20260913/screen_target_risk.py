"""Stop distance tied to mean-reversion opportunity rather than a wide ATR multiple.
5bp market fees, 2bp mean-limit fee, 1bp adverse slippage per side.
"""
import itertools
import pandas as pd
from .research import ROOT,DATA,features,Market
from .screen_regression import regression

m=pd.read_parquet(DATA);b=features(m);train=Market(m,'2021-01-01','2024-01-01');val=Market(m,'2024-01-01','2025-01-01');rows=[]
line,std,slope=regression(b.close,36)
change=b.close.diff();g=change.clip(lower=0).ewm(alpha=.5,adjust=False).mean();loss=(-change.clip(upper=0)).ewm(alpha=.5,adjust=False).mean();rsi=100*g/(g+loss);slow=b.close.rolling(200).mean()
models={
 'boll':((b.high>=b.ma+3*b['std'])&(b.close<b.open)&b.down,b.ma),
 'regression':((slope<0)&(b.close>line+2.5*std)&(b.close<b.open),line),
 'rsi':((slow<slow.shift(12))&(b.close<slow)&(rsi>=85),b.close.rolling(10).mean())}
for name,(signal,target) in models.items():
 for ratio,edge,hours in itertools.product([.25,.5,1.,2.],[.003,.005],[1,4,12]):
  stop=b.close+ratio*(b.close-target);risk=stop/b.close-1
  c=b.loc[signal&(b.close/target-1>=edge)&(risk>=.0015)&(risk<=.03)].assign(stop=stop,target=target)
  tr,_=train.simulate(c,hours,slip=.0001,maker_fee=.0002);va,_=val.simulate(c,hours,slip=.0001,maker_fee=.0002)
  rows.append(dict(model=name,ratio=ratio,edge=edge,hours=hours,**{'train_'+k:v for k,v in tr.items()},**{'val_'+k:v for k,v in va.items()}))
f=pd.DataFrame(rows);f.to_csv(ROOT/'screen_target_risk.csv',index=False)
print(f.sort_values('train_pnl',ascending=False).head(20).to_string(index=False));print('POSITIVE BOTH');print(f.loc[(f.train_pnl>0)&(f.val_pnl>0)].sort_values('train_pnl',ascending=False).to_string(index=False))
