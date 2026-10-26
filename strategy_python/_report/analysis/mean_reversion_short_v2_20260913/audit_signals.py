"""Independent batch reconstruction of every final native entry and risk cap."""
import json
from pathlib import Path
import numpy as np
import pandas as pd
from .research import ROOT,DATA,features

m=pd.read_parquet(DATA);b=features(m)
stop=b.close+4*b.atr;risk=stop/b.close-1
allowed=(b.index.dayofweek>=5)&b.down&(b.high>=b.ma+3*b['std'])&(b.close<b.open)&(b.close/b.ma-1>=.003)&(risk>=.004)&(risk<=.03)
results={}
for label in ('full_3x','full_5x','later_3x'):
 a=json.loads((ROOT/f'native_{label}_audit.json').read_text());p=Path(a['report'])
 params=json.loads((p/'parameters.json').read_text());cap=params['strategy_config']['max_notional_fraction']
 assert params['strategy_config']['weekend_only'] is True
 assert float(params['backtest_spec']['leverage'])==cap
 fills=pd.read_csv(p/'fills.csv');positions=pd.read_csv(p/'positions.csv')
 entries=fills.loc[fills.side=='SELL'];times=pd.DatetimeIndex(pd.to_datetime(entries.ts_last,utc=True))+pd.Timedelta(milliseconds=1)
 assert allowed.reindex(times).all(),'entry not justified by completed 5m data'
 assert np.allclose(entries.avg_px,b.close.reindex(times).to_numpy(),rtol=0,atol=1e-8)
 pnl=positions.realized_pnl.str.split().str[0].astype(float);equity_before=500+pnl.cumsum().shift(fill_value=0)
 exposures=positions.peak_qty*positions.avg_px_open/equity_before
 assert (exposures<=cap+1e-8).all()
 assert (positions.duration_ns<=12*3600_000_000_000).all()
 assert (positions.entry=='SELL').all()
 signal_times=pd.DatetimeIndex(pd.to_datetime(positions.ts_opened,utc=True))+pd.Timedelta(milliseconds=1)
 planned_stop=(stop.reindex(signal_times).to_numpy()*100).astype(np.int64)/100
 planned_loss=positions.peak_qty*(planned_stop-positions.avg_px_open+(.0007*(planned_stop+positions.avg_px_open)))
 assert (planned_loss/equity_before<=.02+1e-7).all()
 winners=fills.loc[fills.tags=="['mean_reversion_short_v2_5min:target']"]
 lookup={str(pid):float(b.ma.loc[t]) for pid,t in zip(entries.position_id,times)}
 assert all(row.avg_px<=lookup[row.position_id]+.011 for row in winners.itertuples())
 results[label]={'entries_verified':len(entries),'five_minute_signals_only':True,
                 'all_entries_in_weekend_signal_window':True,'no_intrabar_retroactive_entry':True,
                 'mean_target_fills_checked':len(winners),'all_short_only':True,
                 'entry_leverage_cap':cap,'entry_leverage_min':float(exposures.min()),
                 'entry_leverage_median':float(exposures.median()),'entry_leverage_max':float(exposures.max()),
                 'max_planned_risk_pct':float((planned_loss/equity_before).max()*100),
                 'max_holding_hours':float(positions.duration_ns.max()/3600_000_000_000)}
(ROOT/'signal_and_risk_audit.json').write_text(json.dumps(results,indent=2)+'\n');print(json.dumps(results,indent=2))
