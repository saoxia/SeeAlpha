"""Publish audited native results with negative recent results shown prominently."""
import hashlib
import json
import os
from pathlib import Path
os.environ.setdefault('MPLCONFIGDIR','/tmp/mean-reversion-matplotlib')
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import pandas as pd

ROOT=Path(__file__).resolve().parent


def main():
 results={k:json.loads((ROOT/f'native_{k}_audit.json').read_text()) for k in ('full_3x','full_5x','later_3x')}
 checked=json.loads((ROOT/'signal_and_risk_audit.json').read_text())
 a=results['full_3x'];b=results['full_5x'];recent=results['later_3x']
 assert a['net_pnl']>0 and b['net_pnl']>0
 fig,axs=plt.subplots(2,1,figsize=(11,7),sharex=True,gridspec_kw={'height_ratios':[3,1]})
 for name,color,style in [('full_3x','#176b87','-'),('full_5x','#d17b26','--')]:
  data=results[name];curve=pd.read_csv(Path(data['report'])/'marked_daily_equity.csv',index_col=0,parse_dates=True)
  axs[0].plot(curve.index,curve.equity-500,color=color,ls=style,lw=1.6,label=f'{name}: +{data["net_pnl"]:.2f} USDT')
  axs[1].plot(curve.index,curve.drawdown_pct,color=color,ls=style,lw=1.)
 axs[0].axhline(0,color='gray',lw=.7)
 axs[0].axvspan(pd.Timestamp('2025-01-01',tz='UTC'),curve.index[-1],color='#888888',alpha=.1,label='Later period: negative return')
 axs[0].set_title('ETHUSDT 5-minute mean-reversion short | weekend entries | after fees')
 axs[0].set_ylabel('Cumulative marked PnL (USDT)');axs[0].legend(loc='upper left')
 axs[1].set_ylabel('Drawdown %')
 for ax in axs:ax.grid(alpha=.15)
 fig.tight_layout();fig.savefig(ROOT/'pnl.png',dpi=160);plt.close(fig)
 ntrials=sum(len(pd.read_csv(p)) for p in ROOT.glob('*.csv') if p.name.startswith(('screen','refine')))
 def row(label,x):return f'| {label} | {x["end_balance"]:.2f} | {x["net_pnl"]:+.2f} | {x["return_pct"]:.2f}% | {x["minute_close_max_drawdown_pct"]:.2f}% | {x["trades"]} | {x["profit_factor"]:.2f} |'
 rows='\n'.join([row('全历史，3倍上限',a),row('全历史，5倍上限',b),row('2025年以后独立运行，3倍上限',recent)])
 annual='\n'.join(f'| {year} | {pnl:+.2f} | {b["annual_closed_pnl"][year]:+.2f} |' for year,pnl in a['annual_closed_pnl'].items())
 costs='\n'.join(f'| 每次成交额额外 {int(bp)/100:.2f}% | {a["extra_slippage_stress"][bp]["net_pnl"]:+.2f} | {b["extra_slippage_stress"][bp]["net_pnl"]:+.2f} | {recent["extra_slippage_stress"][bp]["net_pnl"]:+.2f} |' for bp in ('2','5','10'))
 links='\n'.join(f'- [{name} 原生交互报告]({os.path.relpath(Path(x["report"])/"report.html",ROOT)}) · [逐笔成交]({os.path.relpath(Path(x["report"])/"fills.csv",ROOT)}) · [审计]({os.path.relpath(Path(x["report"])/"audit.json",ROOT)})' for name,x in results.items())
 text=f'''# 五分钟均值回归空头 v2：全历史回测转正，近期仍亏损

已实现独立 `mean_reversion_short_v2_5min`，3～5倍开仓名义杠杆上限。
**它只在UTC周末建仓。全历史为正，但2025年以后的单独回测为负，因此不能称作稳定盈利或样本外通过的策略。**
这是严格五分钟信号和五分钟均值目标；小时线仅提供弱势背景。低频，不是每五分钟都交易。

## 实际结果

ETHUSDT永续、500 USDT初始资金、1分钟行情撮合、3天预热；全部费用按Catalog合约在原生撮合中扣除。
全历史：2021-01-01至2026-09-01；近期：2025-01-01至2026-09-01，结束时间不包含。

| 区间/设置 | 期末USDT | 净收益USDT | 累计收益率 | 分钟收盘权益最大回撤 | 笔数 | 盈利因子 |
|---|---:|---:|---:|---:|---:|---:|
{rows}

3倍版本胜率{a['win_rate_pct']:.2f}%，手续费{a['fees']:.2f} USDT。
收益不是年化。回撤包含每分钟收盘时的浮盈亏，不是单纯已实现回撤，也不是分钟内部最不利价格回撤。
下图每日采样，表中回撤使用全部分钟。

![PnL与回撤](pnl.png)

| 平仓年份 | 3倍上限净收益USDT | 5倍上限净收益USDT |
|---|---:|---:|
{annual}

年度贡献来自各自全历史复利运行，2026年仅包含1～8月；不能与每年独立500 USDT回测混同。

## 固定交易规则

1. 完整五分钟收盘边界位于UTC周六或周日；允许持仓跨到工作日退出。
2. 最近已收盘小时线收盘价≤SMA20，且SMA20≤三根小时前的SMA20。
3. 五分钟最高价≥SMA20＋3σ，收盘价<开盘价，且收盘价至少高于SMA20的0.3%。
4. 收盘确认后市价做空，目标是当时五分钟SMA20，冻结后挂仅减仓限价单。
5. 止损=信号收盘价＋4×五分钟ATR14，距离限定0.4%～3%，否则放弃。
6. 不加仓、单份持仓；12小时到期市价平仓；保护单失效时市价退出。
7. 每笔计划风险2%，双边各0.07%定仓缓冲；按止损反推数量并受名义杠杆限制，另预留保证金费用。

[完整规则与运行方式](../../../mean_reversion_short_v2_5min/README.md) · [固定候选参数](selection_weekend.json)

## 3倍/5倍的含义

账户保证金杠杆与开仓名义金额上限同时设为对应倍数。风险定仓会让实际仓位低于上限，
所以5倍版本不是3倍版本收益乘以5/3。实际开仓名义金额/权益：

| 版本 | 最小 | 中位数 | 最大 |
|---|---:|---:|---:|
| 3倍上限 | {checked['full_3x']['entry_leverage_min']:.3f} | {checked['full_3x']['entry_leverage_median']:.3f} | {checked['full_3x']['entry_leverage_max']:.3f} |
| 5倍上限 | {checked['full_5x']['entry_leverage_min']:.3f} | {checked['full_5x']['entry_leverage_median']:.3f} | {checked['full_5x']['entry_leverage_max']:.3f} |

开仓后的有效杠杆可随市价和权益变化；未完整重放交易所维持保证金阶梯与强平规则。
全部原生入场都核对了对应上限、2%计划风险以及仅做空；不是只把配置字段写成3或5。

## 成本压力

在原生手续费之上，按每次成交额额外扣成本：

| 假设额外成本 | 全期3倍净收益 | 全期5倍净收益 | 近期3倍净收益 |
|---|---:|---:|---:|
{costs}

这是固定成交路径的成本敏感性，不重排成交、数量或信号；不能替代含延迟和真实盘口的重跑。
真实历史资金费率未重放。若额外假设每8小时空头都支付当时名义金额0.01%，并叠加每次成交0.02%额外成本，
全期3倍净收益为{a['2bp_slippage_plus_assumed_funding_net_pnl']:+.2f} USDT；该假设不是历史资金费率。
本地全历史数据有{a['data_gap_count']}处缺口、共{a['missing_minutes']}根缺失分钟，已在审计中列明；指标对缺口重新预热。

## 研究过程与选择偏差

共记录{ntrials}组参数评估，包含重复配置的不同成本口径和敏感性复核，并非{ntrials}个独立策略。
普通布林、挂单、区间轮动、RSI、回归通道、冲高失败和浮盈保护等很多版本失败，全部CSV保留。
一个早期全天候候选在2025年以后亏损而被淘汰，保留区间已经看过一次。
最终周末限制由2021–2023训练及2024验证评分发现，不选择特定年份或个别交易日期；
选0.3%距离而非收益略高的0.5%，以增加交易样本。止损3ATR、5ATR和0.5%距离邻居在开发/验证中也为正。
但2024年最终只有9笔，全期只有95笔，后段仍亏损，**不能宣称研究过程有严格未污染的样本外成功**。
不能仅凭全历史正收益认定适合实盘或已经改善现有三策略组合。

## 验证与产物

策略测试集687项通过；其后新增3/4/5倍入口检查通过，新策略自身20项测试通过；Ruff通过。
覆盖原生止损、跳空、均值止盈、限时退出、保护失效、周末规则、完整指标、数据缺口、
风险/费用预留、非法参数、杠杆入口和组合注册。

[逐笔信号与风险审计](signal_and_risk_audit.json)独立重算每一笔入场，确认均为完成的五分钟信号，
未回溯使用本根高价成交；止盈成交价不高于冻结均值目标，最长持仓12小时。
原生成交现金流、持仓净收益与账户最终余额逐项对齐。

{links}

[全部测试记录](strategy_tests.log) · [新策略测试](new_tests.log) · [源码哈希](source_hashes.json)

```bash
.venv/bin/python -m strategy_python.mean_reversion_short_v2_5min.backtest --start 2021-01-01 --end 2026-09-01 --max-leverage 3
.venv/bin/python -m strategy_python.mean_reversion_short_v2_5min.backtest --start 2021-01-01 --end 2026-09-01 --max-leverage 5
```

组合注册名`mean_reversion_short_v2_5min`，仅支持`risk.mode=none`；没有自动添加到现有组合清单。
'''
 (ROOT/'结果说明.md').write_text(text)
 status='''# 已完成原生回测验证：全历史正收益，近期负收益

最终策略 mean_reversion_short_v2_5min，五分钟信号/均值目标，UTC周末建仓，3～5倍开仓名义上限。
默认3倍，单笔计划风险2%，不加仓，12小时市价退出。全期3倍+70.65 USDT、5倍+70.89 USDT；
2025年以来独立3倍回测-11.52 USDT。不能称作稳定盈利或样本外验证通过。
详见结果说明.md、signal_and_risk_audit.json和native_*_audit.json。
原selection.json全天候候选已淘汰；selection_weekend.json才是当前默认参数来源。
'''
 (ROOT/'STATUS.md').write_text(status)
 selection=json.loads((ROOT/'selection_weekend.json').read_text());selection['status']='native full-history profitable for 3x and 5x caps; later period negative; research-only evidence'
 (ROOT/'selection_weekend.json').write_text(json.dumps(selection,indent=2)+'\n')
 old=json.loads((ROOT/'selection.json').read_text());old['status']='REJECTED: full and reserved periods negative; never promoted to final defaults'
 (ROOT/'selection.json').write_text(json.dumps(old,indent=2)+'\n')
 files=list(Path('strategy_python/mean_reversion_short_v2_5min').glob('*.py'))+[Path('strategy_python/portfolio.py'),ROOT/'selection_weekend.json',ROOT/'run_native.py',ROOT/'audit_signals.py']
 (ROOT/'source_hashes.json').write_text(json.dumps({str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in files},indent=2)+'\n')
 print(rows,flush=True);print(costs,flush=True);print(ROOT/'结果说明.md')

if __name__=='__main__':main()
