"""Read-only total diagnosis of the latest eight logical slots."""
import ast
import hashlib
import json
from pathlib import Path
import numpy as np
import pandas as pd

ROOT=Path(__file__).resolve().parent
PROJECT=ROOT.parents[2]
BASE=ROOT.parent/'mechanism_full_20260915/runs/full'
SLOTS={
 'MRL_original':'BTC_MRL_original','MRL_optimized':'BTC_MRL_optimized',
 'TFL_original':'BTC_TFL','TFL_optimized':'BTC_TFL',
 'TFS_original':'BTC_TFS','TFS_optimized':'BTC_TFS',
 'PBS_original':'BTC_PBS','PBS_optimized':'BTC_PBS',
}

def analyze(key):
    directory=BASE/key
    m=json.loads((directory/'metrics.json').read_text())
    f=pd.read_csv(directory/'fills.csv')
    f['time']=pd.to_datetime(f.ts_last,utc=True)
    f['notional']=f.filled_qty.astype(float)*f.avg_px.astype(float)
    f['fee']=f.commissions.map(lambda s:sum(float(x.split()[0]) for x in ast.literal_eval(s)))
    f['flow']=np.where(f.side=='SELL',1,-1)*f.notional-f.fee
    trades=[]
    for pid,g in f.groupby('position_id',sort=False):
        g=g.sort_values('time',kind='stable')
        entry=g[g.side==g.side.iloc[0]]
        trades.append({'position_id':pid,'entry':str(g.time.iloc[0]),'exit':str(g.time.iloc[-1]),'hours':(g.time.iloc[-1]-g.time.iloc[0]).total_seconds()/3600,'net_pnl':g.flow.sum(),'fees':g.fee.sum(),'entry_notional':entry.notional.sum(),'unit_net':g.flow.sum()/entry.notional.sum(),'exit_tags':g.tags.iloc[-1]})
    t=pd.DataFrame(trades)
    assert abs(t.net_pnl.sum()-m['net_pnl'])<1e-4
    t.to_csv(ROOT/f'{key}_trades.csv',index=False)
    wins=t[t.net_pnl>0];losses=t[t.net_pnl<0]
    daily=pd.read_csv(directory/'daily_equity.csv',index_col=0,parse_dates=True)
    below=daily.equity<daily.equity.cummax()-1e-8
    runs=below.groupby((below!=below.shift()).cumsum()).sum()
    monthly=f.groupby(f.time.dt.strftime('%Y-%m')).size()
    row={'baseline':key,**{k:m[k] for k in ['net_pnl','return_pct','max_drawdown_pct','fees','cashflow_trades','cashflow_win_rate_pct','payoff_ratio']},'gross_before_fees':m['net_pnl']+m['fees'],'average_win':wins.net_pnl.mean(),'average_loss':losses.net_pnl.mean(),'worst_trade':t.net_pnl.min(),'best_trade':t.net_pnl.max(),'worst_5_total':t.net_pnl.nsmallest(5).sum(),'best_5_total':t.net_pnl.nlargest(5).sum(),'median_hours':t.hours.median(),'p95_hours':t.hours.quantile(.95),'max_hours':t.hours.max(),'longest_underwater_days':int(runs.max()),'active_months':len(monthly),'months_total':68,'unit_net_mean':t.unit_net.mean(),'unit_payoff':wins.unit_net.mean()/-losses.unit_net.mean(),'extra_5bp_net_pnl':m['stress']['5']['net_pnl']}
    durations=[]
    for interval,g in t.groupby(pd.cut(t.hours,[0,6,24,48,168,float('inf')],include_lowest=True),observed=True):
        durations.append({'baseline':key,'holding_hours_bin':str(interval),'trades':len(g),'net_pnl':g.net_pnl.sum(),'fees':g.fees.sum(),'win_rate':(g.net_pnl>0).mean()})
    return row,durations


def main():
    frozen={str(p.relative_to(PROJECT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in (PROJECT/'strategy_python').rglob('*') if p.is_file() and p.suffix in ('.py','.json') and ROOT not in p.parents}
    dest=ROOT/'prior_files_sha256.json'
    if dest.exists():
        assert json.loads(dest.read_text())==frozen,'Existing files changed since cycle start'
    else:dest.write_text(json.dumps(frozen,indent=2)+'\n')
    results={};duration=[]
    for key in dict.fromkeys(SLOTS.values()):
        results[key],d=analyze(key);duration+=d
    rows=[{'slot':slot,**results[key]} for slot,key in SLOTS.items()]
    pd.DataFrame(rows).to_csv(ROOT/'total_diagnosis.csv',index=False)
    pd.DataFrame(duration).to_csv(ROOT/'holding_diagnosis.csv',index=False)
    (ROOT/'slots.json').write_text(json.dumps(SLOTS,indent=2)+'\n')
    lines=['# 总：最新机制八位置起点诊断','','八位置中趋势多/空和PBS各两位置基线重复，不能把它们当独立证据。全历史含2021—2023机制训练期。','','| 位置 | 收益率 | 回撤 | 胜率 | 金额盈亏比 | 最坏5笔合计 | 费用 | 持仓中位小时 | 最长持仓小时 |','|---|---:|---:|---:|---:|---:|---:|---:|---:|']
    for r in rows:lines.append(f"| {r['slot']} | {r['return_pct']:.2f}% | {r['max_drawdown_pct']:.2f}% | {r['cashflow_win_rate_pct']:.2f}% | {r['payoff_ratio']:.4f} | {r['worst_5_total']:.2f} | {r['fees']:.2f} | {r['median_hours']:.2f} | {r['max_hours']:.2f} |")
    lines+=['','[全部统计](total_diagnosis.csv) · [按持仓时长分组](holding_diagnosis.csv)。持仓时长分组是事后诊断，不代表可直接按未来时长选择交易。回撤持续时间按每日收盘计算，仅用于描述；最终最大回撤仍取分钟盯市。','', '每笔交易CSV保留净现金流、首末成交时间和退出标签，后续假设必须回到真实成交与新的原生回测验证。']
    (ROOT/'TOTAL_DIAGNOSIS.md').write_text('\n'.join(lines)+'\n')
    print('TOTAL_DIAGNOSIS_COMPLETE',len(rows),'frozen',len(frozen))

if __name__=='__main__':main()
