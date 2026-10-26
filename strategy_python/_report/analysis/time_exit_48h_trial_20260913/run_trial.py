from datetime import datetime, UTC
from decimal import Decimal
from pathlib import Path
import sys, json, hashlib
import pandas as pd

from strategy_python._backtesting import run_backtest
from strategy_python.mean_reversion_short_5min import MeanReversionShort5MinStrategy, MeanReversionShort5MinConfig
from strategy_python.mean_reversion_short_5min.backtest import BACKTEST_SPEC

root=Path('/home/lly/NautilusTrader')
base=root/'strategy_python/_report/analysis/time_exit_48h_trial_20260913'
mode=sys.argv[1]
assert mode in ('gated_interval_cap25', 'step1_cap5')
start=datetime(2026,1,1,tzinfo=UTC);end=datetime(2026,9,1,tzinfo=UTC)
points=[]

class ObserveStrategy(MeanReversionShort5MinStrategy):
    def _on_five_minute_bar(self, bar):
        super()._on_five_minute_bar(bar)
        if bar.ts_end>=self.config.trade_start_ns:
            points.append((bar.ts_end, float(self._account_equity())))

strategy=ObserveStrategy(MeanReversionShort5MinConfig(
    instrument_id=BACKTEST_SPEC.instrument_id,bar_type=BACKTEST_SPEC.bar_type,
    trade_start_ns=int(start.timestamp()*1_000_000_000),
    time_exit_loss_step=.01, time_exit_max_loss=.25 if mode=='gated_interval_cap25' else .05,
    time_gated_stop_trial=mode=='gated_interval_cap25',
))
output=run_backtest(start,end,strategy,'mean_reversion_short_5min',BACKTEST_SPEC,
                    report_dir=base/mode,check_data=False)
assert output
curve=pd.DataFrame(points,columns=['timestamp_ns','equity_usdt'])
curve['time_utc']=pd.to_datetime(curve.timestamp_ns,utc=True)
curve.to_csv(output/'equity_5min.csv',index=False)
(base/f'{mode}_output.json').write_text(json.dumps({'report':str(output)},indent=2))
print('TRIAL_REPORT',output,flush=True)
