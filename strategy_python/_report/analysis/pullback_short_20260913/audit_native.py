"""Reconcile native fills and reconstruct minute-close marked equity.
Extra-slippage stress keeps native trade paths fixed; it is not a rerun.
"""
import argparse
import ast
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT=Path(__file__).resolve().parent


def audit(path):
    p=Path(path);params=json.loads((p/'parameters.json').read_text());summary=json.loads((p/'summary.json').read_text())
    fills=pd.read_csv(p/'fills.csv');positions=pd.read_csv(p/'positions.csv')
    assert (fills.status=='FILLED').all(), 'audit requires fully filled orders'
    assert (positions.side=='FLAT').all(), 'open positions must be explicitly marked'
    fills['time']=pd.to_datetime(fills.ts_last,utc=True)
    fills['fee']=fills.commissions.map(lambda x:sum(float(v.split()[0]) for v in ast.literal_eval(x)))
    fills['notional']=fills.filled_qty*fills.avg_px
    fills['cash_delta']=np.where(fills.side=='SELL',fills.notional,-fills.notional)-fills.fee
    fills['qty_delta']=np.where(fills.side=='SELL',fills.filled_qty,-fills.filled_qty)
    events=fills.groupby('time')[['cash_delta','qty_delta','notional']].sum()
    minutes=pd.read_parquet(ROOT/'minutes.parquet');minutes.index-=pd.Timedelta(milliseconds=1)
    minutes=minutes.loc[(minutes.index>=pd.Timestamp(params['start']))&(minutes.index<pd.Timestamp(params['end']))]
    assert events.index.isin(minutes.index).all(), 'fill does not align to minute observation'
    event_series=events.reindex(minutes.index,fill_value=0)
    cash=500+event_series.cash_delta.cumsum();quantity=event_series.qty_delta.cumsum()
    assert quantity.min()>-1e-8 and abs(quantity.iloc[-1])<1e-8
    equity=cash-quantity*minutes.close
    end_balance=float(summary['summary']['account.BINANCE.balance.USDT.total'].split()[0])
    assert abs(equity.iloc[-1]-end_balance)<1e-5,(equity.iloc[-1],end_balance)
    dd=equity/equity.cummax().clip(lower=500)-1
    pnls=positions.realized_pnl.str.split().str[0].astype(float)
    assert abs(pnls.sum()-(end_balance-500))<1e-5
    exit_time=pd.to_datetime(positions.ts_closed,utc=True)
    annual=pnls.groupby(exit_time.dt.year).sum().to_dict()
    annual_equity=equity.resample('YE').last();annual_equity_change=annual_equity.diff();annual_equity_change.iloc[0]=annual_equity.iloc[0]-500
    notional_cumulative=event_series.notional.cumsum()
    stress={}
    for bps in (2,5,10):
        eq=equity-notional_cumulative*bps/10000
        stress[str(bps)]={'extra_slippage_bps_per_fill':bps,'net_pnl':float(eq.iloc[-1]-500),
                          'minute_close_max_drawdown_pct':float((1-eq/eq.cummax().clip(lower=500)).max()*100)}
    # Sensitivity only: shorts pay 1bp of then-current notional at each 8h timestamp.
    funding_times=(minutes.index+pd.Timedelta(milliseconds=1)).hour%8==0
    funding_times &= (minutes.index+pd.Timedelta(milliseconds=1)).minute==0
    assumed_funding=float((quantity[funding_times]*minutes.close[funding_times]*.0001).sum())
    gaps=minutes.index.to_series().diff();bad=gaps[gaps>pd.Timedelta(minutes=1)]
    stats={'report':str(p),'start':params['start'],'end':params['end'],'initial_balance':500,
           'end_balance':end_balance,'net_pnl':end_balance-500,'return_pct':(end_balance/500-1)*100,
           'trades':len(positions),'profit_factor':float(pnls[pnls>0].sum()/-pnls[pnls<0].sum()),
           'win_rate_pct':float((pnls>0).mean()*100),'fees':float(fills.fee.sum()),
           'minute_close_max_drawdown_pct':float(-dd.min()*100),'drawdown_trough':str(dd.idxmin()),
           'annual_closed_pnl':annual,'annual_marked_pnl':{str(k.year):float(v) for k,v in annual_equity_change.items()},
           'max_hold_hours':float(positions.duration_ns.max()/3600_000_000_000),
           'exit_reasons':fills.loc[fills.side=='BUY','tags'].value_counts().to_dict(),
           'extra_slippage_stress':stress,'funding_mode':'not simulated; sensitivity is not historical funding',
           'assumed_paying_1bp_each_8h_cost':assumed_funding,
           '2bp_slippage_plus_assumed_funding_net_pnl':stress['2']['net_pnl']-assumed_funding,
           'data_gap_count':len(bad),'missing_minutes':int((bad/pd.Timedelta(minutes=1)-1).sum()),
           'gaps':{str(t):int(v/pd.Timedelta(minutes=1)-1) for t,v in bad.items()},
           'equity_method':'Minute closing mark, includes commissions and unrealized PnL; not intraminute worst equity. Slippage sensitivity does not re-execute orders.'}
    daily=pd.DataFrame({'equity':equity,'short_qty':quantity,'drawdown_pct':dd*100}).resample('1D').last()
    daily.to_csv(p/'marked_daily_equity.csv')
    (p/'audit.json').write_text(json.dumps(stats,indent=2)+'\n')
    print(json.dumps(stats,indent=2))
    return stats

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('report');args=parser.parse_args();audit(args.report)
