"""Necessary first-unit risk at every complete BOLL quote, independent of alpha gates."""
import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT=Path(__file__).resolve().parent
BASE=ROOT.parent/'mechanism_full_20260915/runs/full'

def main():
    f=pd.read_parquet(ROOT.parent/'mechanism_20260915/BTC_5m.parquet').sort_index()
    segments=(f.index.to_series().diff()!=pd.Timedelta(minutes=5)).cumsum()
    mid=f.close.groupby(segments).transform(lambda x:x.rolling(20).mean())
    std=f.close.groupby(segments).transform(lambda x:x.rolling(20).std(ddof=0))
    rows=[]
    for key,k in [('BTC_MRL_original__flat_risk',2),('BTC_MRL_optimized__flat_risk',2.25)]:
        directory=BASE/key
        params=json.loads((directory/'parameters.json').read_text());c=params['config']
        daily=pd.read_csv(directory/'daily_equity.csv',index_col=0,parse_dates=True)
        equity=float(daily[daily.index<pd.Timestamp('2025-01-01',tz='UTC')].equity.iloc[-1])
        # Recovery can only lower this budget; use its maximal value one.
        budget=equity*c['cycle_risk_fraction']*c['mechanism_flat_multiplier']
        p1=mid-k*std;gap=mid/p1-1
        offsets=[np.maximum(2*c['entry_fee_rate'],gap*x) for x in c['mechanism_add_multipliers']]
        p2=p1*(1-offsets[0]);p3=(p1+p2)/2*(1-offsets[1]);p4=(p1+p2+p3)/3*(1-offsets[2])
        avg=(p1+p2+p3+p4)/4;stop=avg*(1-c['hard_stop_distance'])
        risk=((avg-stop)*4+(p1+p2+p3+p4)*c['entry_fee_rate']+stop*4*c['forced_exit_fee_rate'])*(1+c['stop_cost_buffer'])*float(params['size_increment'])
        for year in [2025,2026]:
            a=risk[(risk.index.year==year)&risk.notna()&(p4>0)]
            rows.append({'baseline':key,'year':year,'frozen_start_equity':equity,'maximum_risk_budget':budget,'minimum_one_lot_cycle_risk':a.min(),'quote_count':len(a),'quotes_able_to_fund_one_lot_at_start_equity':int((a<=budget).sum()),'size_increment':params['size_increment']})
    pd.DataFrame(rows).to_csv(ROOT/'lot_constraint.csv',index=False)
    lines=['# 空：最小下单数量造成的交易停止','','BTC合约数量步长为0.001。MRL预算先覆盖满四层止损及费用，再把每层数量向下取整。资金和预算不足一个步长时，返回无可下单数量。以下在每个完整5分钟BOLL报价计算四层计划的最小一档风险，不要求入场信号通过，因此是可交易性的必要条件。','','采用2024末权益并把恢复系数设为最大值1。2025各自无交易，冻结权益与实际一致；2026原版恢复交易后权益变化，表内只是按年初不变权益的可行性检查，不能当全年实际订单统计。','','| 配置 | 年份 | 风险预算上限 | 最便宜一档所需周期风险 | 可负担报价数/全部 |','|---|---|---:|---:|---:|']
    for r in rows:lines.append(f"| {r['baseline']} | {r['year']} | {r['maximum_risk_budget']:.2f} | {r['minimum_one_lot_cycle_risk']:.2f} | {r['quotes_able_to_fund_one_lot_at_start_equity']}/{r['quote_count']} |")
    lines+=['','这解释了为什么低风险曲线可能来自交易数量被取整成零，而非持续交易更稳健。后续空间实验必须记录实际名义暴露、最小数量可行性及活跃月份；不会向上取整强行突破预算，也不会把提高账户保证金杠杆误当提高策略预算。','', '[逐项数值](lot_constraint.csv)。指标用连续完整条形窗口，缺口重置；真实信号/恢复状态可进一步减少机会，不能增加必要预算不足时的可交易数量。']
    (ROOT/'LOT_CONSTRAINT.md').write_text('\n'.join(lines)+'\n')
    print(pd.DataFrame(rows).to_string(index=False))

if __name__=='__main__':main()
