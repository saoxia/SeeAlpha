from pathlib import Path
import json
import numpy as np
import pandas as pd

base=Path('/home/lly/NautilusTrader/strategy_python/_report/analysis/time_exit_48h_trial_20260913')
results=[]
for mode in ['step1_cap5','gated_interval_cap25']:
    report=Path(json.loads((base/f'{mode}_output.json').read_text())['report'])
    p=pd.read_csv(report/'positions.csv')
    f=pd.read_csv(report/'fills.csv')
    account=pd.read_csv(report/'account.csv')
    eq=pd.read_csv(report/'equity_5min.csv')
    pnl=p.realized_pnl.str.split().str[0].astype(float)
    hours=p.duration_ns/3_600_000_000_000
    equity=np.r_[500.,eq.equity_usdt.to_numpy(),float(account.total.iloc[-1])]
    peak=np.maximum.accumulate(equity)
    dd=(peak-equity)/peak*100
    max_idx=int(np.argmax(dd))
    curve=pd.DataFrame({'equity_usdt':equity,'drawdown_pct':dd})
    curve.to_csv(base/f'{mode}_drawdown.csv',index=False)
    closed=np.r_[500.,500.+p.assign(_pnl=pnl).sort_values('ts_closed')._pnl.cumsum().to_numpy()]
    cp=np.maximum.accumulate(closed)
    result={
        'mode':mode,'report':str(report),'start':'2026-01-01','end_exclusive':'2026-09-01',
        'initial_balance':500.,'final_balance':float(account.total.iloc[-1]),
        'net_pnl':float(pnl.sum()),'return_pct':(float(account.total.iloc[-1])/500.-1)*100,
        'closed_positions':len(p),'filled_orders':len(f),'win_rate_pct':float((pnl>0).mean()*100),
        'average_holding_hours':float(hours.mean()),'median_holding_hours':float(hours.median()),
        'max_holding_hours':float(hours.max()),'over_24h_positions':int((hours>24).sum()),
        'over_72h_positions':int((hours>72).sum()),'over_7d_positions':int((hours>168).sum()),
        'max_drawdown_pct_5min_mtm':float(dd.max()),
        'max_drawdown_pct_closed_positions':float(((cp-closed)/cp*100).max()),
        'filled_notional_usdt':float((f.filled_qty*f.avg_px).sum()),
        'worst_position_pnl':float(pnl.min()),
    }
    assert abs(500+pnl.sum()-float(account.total.iloc[-1]))<1e-5
    # Find the cycle discussed by the user, using its entry time rather than generated IDs.
    focus=p[p.ts_opened.str.startswith('2026-04-13 12:53:59')]
    if len(focus):
        r=focus.iloc[0]
        result['april13_position']={'opened':r.ts_opened,'closed':r.ts_closed,'pnl':r.realized_pnl,
                                     'holding_hours':float(r.duration_ns/3_600_000_000_000)}
    results.append(result)
(base/'comparison.json').write_text(json.dumps(results,ensure_ascii=False,indent=2))
pd.DataFrame([{k:v for k,v in r.items() if not isinstance(v,dict)} for r in results]).to_csv(base/'comparison.csv',index=False,encoding='utf-8-sig')
labels=[('net_pnl','净盈亏（USDT）',2),('return_pct','收益率（%）',2),('closed_positions','平仓周期数',0),('filled_orders','成交订单数',0),('average_holding_hours','平均持仓（小时）',2),('median_holding_hours','中位持仓（小时）',2),('max_holding_hours','最长持仓（小时）',2),('over_72h_positions','超过72小时的持仓数',0),('over_7d_positions','超过7天的持仓数',0),('max_drawdown_pct_5min_mtm','最大回撤（5分钟含浮盈亏，%）',2),('max_drawdown_pct_closed_positions','最大回撤（平仓曲线，%）',2),('filled_notional_usdt','买卖累计成交额（USDT）',2),('win_rate_pct','胜率（%）',2)]
lines=['# 递增更新间隔的时间退出规则对比','','区间：2026-01-01 至 2026-09-01（UTC，结束日不含）。ETHUSDT 永续，初始500 USDT，5倍杠杆，1分钟输入，15天预热。旧版：超过24小时均价退出、48小时起每天增加1%至5%，保留30%硬止损。试验版：首仓首次成交计时、加仓重置，48小时1%，后续间隔1/2/3/4……天分别增加1/2/3/4……个百分点，上限25%；取消首仓/两份即时硬止损，保留余仓保护。行情、费用、定仓口径相同。','','| 指标 | 旧版：每天更新，上限5% | 试验：递增间隔，上限25% |','|---|---:|---:|']
for key,label,precision in labels:
    lines.append(f'| {label} | {results[0][key]:,.{precision}f} | {results[1][key]:,.{precision}f} |')
lines+=['','含浮盈亏权益每5分钟采样，最大回撤不代表逐笔或分钟内的最坏回撤。净盈亏包含回测手续费；持仓统计包含回测末尾清仓。阶梯退出价是限价目标，不保证到期成交，也不保证实际亏损不超过设定比例。','','## 报告位置']
for r in results:lines.append(f"- {r['mode']}: {r['report']}")
(base/'对比结果.md').write_text('\n'.join(lines)+'\n')
print(json.dumps(results,ensure_ascii=False,indent=2))
