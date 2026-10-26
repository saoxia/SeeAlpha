"""Conservative visible-hour context for baseline actual trades; descriptive only."""
import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT=Path(__file__).resolve().parent


def main():
    h=pd.read_parquet(ROOT.parent/'mechanism_20260915/BTC_60m.parquet').sort_index()
    segments=(h.index.to_series().diff()!=pd.Timedelta(hours=1)).cumsum()
    prior=h.close.groupby(segments).shift()
    tr=pd.concat([h.high-h.low,(h.high-prior).abs(),(h.low-prior).abs()],axis=1).max(axis=1)
    tr[prior.isna()]=np.nan
    h['atr']=tr.groupby(segments).transform(lambda s:s.rolling(14).mean())
    h['ma20']=h.close.groupby(segments).transform(lambda s:s.rolling(20).mean())
    h['ma99']=h.close.groupby(segments).transform(lambda s:s.rolling(99).mean())
    h['slope6']=h.ma20-h.ma20.groupby(segments).shift(6)
    h['hour_time']=h.index
    summaries=[];allrows=[];exclusions={}
    for key in ['BTC_MRL_original','BTC_MRL_optimized','BTC_TFL','BTC_TFS','BTC_PBS']:
        t=pd.read_csv(ROOT/f'{key}_trades.csv')
        t['entry_time']=pd.to_datetime(t.entry,utc=True).dt.as_unit('ns');t['exit_time']=pd.to_datetime(t.exit,utc=True).dt.as_unit('ns')
        t=t.sort_values('entry_time')
        joined=pd.merge_asof(t,h.reset_index(drop=True).sort_values('hour_time'),left_on='entry_time',right_on='hour_time',direction='backward',allow_exact_matches=False)
        joined['period']=np.where(joined.entry_time<pd.Timestamp('2024-01-01',tz='UTC'),'develop',np.where(joined.entry_time<pd.Timestamp('2025-01-01',tz='UTC'),'validation','review'))
        end=pd.to_datetime(joined.period.map({'develop':'2024-01-01','validation':'2025-01-01','review':'2026-08-31'}),utc=True)
        valid=(joined.exit_time<end)&((joined.entry_time-joined.hour_time)<=pd.Timedelta(hours=2))&joined.atr.notna()&joined.ma99.notna()
        exclusions[key]=int((~valid).sum());joined=joined[valid].copy()
        direction=-1 if key in ['BTC_TFS','BTC_PBS'] else 1
        joined['aligned_ma20']=direction*(joined.close-joined.ma20)>0
        joined['aligned_ma99']=direction*(joined.close-joined.ma99)>0
        joined['aligned_slope6']=direction*joined.slope6>0
        joined['atr_return']=joined.atr/joined.close
        cuts=joined.loc[joined.period=='develop','atr_return'].quantile([1/3,2/3]).tolist()
        joined['volatility_band']=pd.cut(joined.atr_return,[-np.inf,*cuts,np.inf],labels=['low','middle','high'])
        joined['baseline']=key;allrows.append(joined)
        for feature in ['aligned_ma20','aligned_ma99','aligned_slope6','volatility_band']:
            for (period,label),g in joined.groupby(['period',feature],observed=True):
                wins=g[g.net_pnl>0];losses=g[g.net_pnl<0]
                summaries.append({'baseline':key,'period':period,'feature':feature,'value':str(label),'trades':len(g),'months':g.entry_time.dt.strftime('%Y-%m').nunique(),'net_pnl':g.net_pnl.sum(),'net_per_entry_notional':g.net_pnl.sum()/g.entry_notional.sum(),'unit_net_mean':g.unit_net.mean(),'win_rate':(g.net_pnl>0).mean(),'mean_win':wins.net_pnl.mean(),'mean_loss':losses.net_pnl.mean()})
    pd.DataFrame(summaries).to_csv(ROOT/'entry_context_summary.csv',index=False)
    pd.concat(allrows,ignore_index=True).to_parquet(ROOT/'entry_context_trades.parquet',index=False)
    (ROOT/'entry_context_exclusions.json').write_text(json.dumps(exclusions,indent=2)+'\n')
    lines=['# 入场背景诊断：原实际交易','','只使用严格早于首笔成交时间的完整小时数据，遇缺口重置ATR/MA；这是保守的成交时背景近似，不是精确复现此前限价报价时特征。超过2小时陈旧背景及跨开发/检验边界持仓已排除。波动分箱仅按开发期三分位划分。','','仅用于提出假设：筛选后交易不再保证仍有原持仓互斥、退出与资金路径，不能把分组净收益当新策略回测或统计显著结论。下一步必须使用当时真实可见特征进行原生策略验证。','','[分组数据](entry_context_summary.csv) · [排除计数](entry_context_exclusions.json)','','| 基线 | 开发期状态 | 样本 | 单位名义平均净收益 | 净收益 |','|---|---|---:|---:|---:|']
    for row in summaries:
        if row['period']=='develop' and row['feature'] in ['aligned_slope6','aligned_ma99']:
            lines.append(f"| {row['baseline']} | {row['feature']}={row['value']} | {row['trades']} | {row['unit_net_mean']:.3%} | {row['net_pnl']:.2f} |")
    (ROOT/'ENTRY_CONTEXT.md').write_text('\n'.join(lines)+'\n')
    print('ENTRY_CONTEXT_AUDIT',len(summaries))

if __name__=='__main__':main()
