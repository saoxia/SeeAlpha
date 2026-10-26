"""Local sensitivity for broad weekend-only entry regime (no individual dates).
Not pristine OOS: a previous all-days candidate already failed on 2025+.
"""
import itertools
import pandas as pd
from .research import ROOT,DATA,features,candidates,Market
m=pd.read_parquet(DATA);b=features(m);train=Market(m,'2021-01-01','2024-01-01');val=Market(m,'2024-01-01','2025-01-01');rows=[]
for z,edge,stopatr,hours in itertools.product([2.,2.5,3.],[.003,.005],[3.,4.,5.],[4,12]):
 c=candidates(b,'wick',z,edge,'down',stopatr);c=c.loc[c.index.dayofweek>=5]
 tr,_=train.simulate(c,hours,slip=.0001,maker_fee=.0002);va,_=val.simulate(c,hours,slip=.0001,maker_fee=.0002)
 rows.append(dict(z=z,edge=edge,stopatr=stopatr,hours=hours,**{'train_'+k:v for k,v in tr.items()},**{'val_'+k:v for k,v in va.items()}))
f=pd.DataFrame(rows);f.to_csv(ROOT/'refine_weekend.csv',index=False)
print(f.sort_values('train_pnl',ascending=False).head(20).to_string(index=False));print('POSITIVE BOTH');print(f.loc[(f.train_pnl>0)&(f.val_pnl>0)].sort_values('train_pnl',ascending=False).to_string(index=False))
