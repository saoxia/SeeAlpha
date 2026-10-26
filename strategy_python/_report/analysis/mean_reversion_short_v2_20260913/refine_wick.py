"""Local sensitivity around the one training-positive 5m rejection family.
Still only 2021-23 development and 2024 validation, all trials logged.
"""
import itertools
import pandas as pd
from .research import ROOT,DATA,features,candidates,Market
m=pd.read_parquet(DATA);b=features(m);train=Market(m,'2021-01-01','2024-01-01');val=Market(m,'2024-01-01','2025-01-01');rows=[]
for z,edge,stopatr,hours in itertools.product([2.5,3.,3.5],[.004,.005,.006],[3.,4.,5.],[4,8,12]):
 c=candidates(b,'wick',z,edge,'down',stopatr)
 tr,_=train.simulate(c,hours);va,_=val.simulate(c,hours)
 rows.append(dict(z=z,edge=edge,stopatr=stopatr,hours=hours,**{'train_'+k:v for k,v in tr.items()},**{'val_'+k:v for k,v in va.items()}))
f=pd.DataFrame(rows);f.to_csv(ROOT/'refine_wick.csv',index=False)
print(f.sort_values('train_pnl',ascending=False).head(20).to_string(index=False));print('POSITIVE BOTH');print(f.loc[(f.train_pnl>0)&(f.val_pnl>0)].sort_values('train_pnl',ascending=False).to_string(index=False))
