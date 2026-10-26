import ast,json
from pathlib import Path
import pandas as pd
import numpy as np
R=Path('/home/lly/NautilusTrader/strategy_python/_report/analysis/portfolio_pullback_3x_20260913')
p=Path(json.loads((R/'comparison.json').read_text())['mean_reversion_long_trend_both_pullback_3x']['report'])
f=pd.read_csv(p/'fills.csv');f['time']=pd.to_datetime(f.ts_last,utc=True);f['fee']=f.commissions.map(lambda x:sum(float(v.split()[0]) for v in ast.literal_eval(x)));f['q']=np.where(f.side=='BUY',f.filled_qty,-f.filled_qty);f['cash']=-f.q*f.avg_px-f.fee
price=pd.read_parquet(R.parent/'pullback_short_20260913/minutes.parquet')['close'];price.index-=pd.Timedelta(milliseconds=1);price=price.loc['2021-01-01':'2022-07-01']
parts={};qty={}
for name,g in f.groupby('strategy_id'):
 e=g.groupby('time')[['q','cash']].sum().reindex(price.index,fill_value=0)
 qty[name]=e.q.cumsum();parts[name]=e.cash.cumsum()+qty[name]*price
v=pd.DataFrame(parts);q=pd.DataFrame(qty);eq=500+v.sum(axis=1);dd=eq/eq.cummax().clip(lower=500)-1;trough=dd.idxmin();peak=eq.loc[:trough].idxmax();mask=(f.time>peak)&(f.time<=trough)
r={'peak':str(peak),'trough':str(trough),'peak_equity':float(eq[peak]),'trough_equity':float(eq[trough]),'loss':float(eq[trough]-eq[peak]),'drawdown_pct':float(-dd[trough]*100),'eth_peak':float(price[peak]),'eth_trough':float(price[trough]),'contribution':{},'worst_positions':[]}
for name in v:
 g=f.loc[mask&(f.strategy_id==name)]
 r['contribution'][name]={'equity_change':float(v.loc[trough,name]-v.loc[peak,name]),'qty_peak':float(q.loc[peak,name]),'qty_trough':float(q.loc[trough,name]),'fees':float(g.fee.sum()),'fills':len(g),'time_in_market_pct':float((q.loc[peak:trough,name].abs()>1e-9).mean()*100)}
pos=pd.read_csv(p/'positions.csv');pos['closed']=pd.to_datetime(pos.ts_closed,utc=True);pos['pnl']=pos.realized_pnl.str.split().str[0].astype(float);sel=pos[(pos.closed>peak)&(pos.closed<=trough)]
for _,row in sel.nsmallest(12,'pnl').iterrows():
 fs=f[f.position_id==row.position_id].sort_values('time')
 r['worst_positions'].append({'id':row.position_id,'strategy':row.strategy_id,'opened':row.ts_opened,'closed':row.ts_closed,'pnl':row.pnl,'peak_qty':row.peak_qty,'open_price':row.avg_px_open,'close_price':row.avg_px_close,'exit_tags':fs.iloc[-1].tags,'fills':fs[['time','side','filled_qty','avg_px','tags']].astype(str).to_dict('records')})
# Per-position equity contribution handles positions crossing peak/trough.
pr=[]
for pid,g in f[f.time<=trough].groupby('position_id'):
 a=g[g.time<=peak];b=g[g.time<=trough];x=float(b.cash.sum()+b.q.sum()*price[trough]-a.cash.sum()-a.q.sum()*price[peak]);
 if abs(x)>.01:pr.append({'id':pid,'strategy':g.iloc[0].strategy_id,'change':x})
r['worst_marked_positions']=sorted(pr,key=lambda x:x['change'])[:12]
r['closed_pnl_by_strategy']=sel.groupby('strategy_id').pnl.agg(['sum','count']).to_dict('index')
daily=v.loc[peak:trough].resample('1D').last()-v.loc[peak];daily.to_csv(R/'drawdown_contributions_daily.csv')
(R/'drawdown_analysis.json').write_text(json.dumps(r,indent=2)+'\n');print(json.dumps({k:v for k,v in r.items() if k!='worst_positions'},indent=2));print('WORST CLOSED');print(sel.nsmallest(12,'pnl')[['strategy_id','ts_opened','ts_closed','pnl']].to_string(index=False))
