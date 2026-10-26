"""Create a concise review artifact from audited, completed native runs."""
from pathlib import Path
import hashlib
import json
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

ROOT=Path(__file__).resolve().parent
full=json.loads((ROOT/'native_full_audit.json').read_text());hold=json.loads((ROOT/'native_holdout_audit.json').read_text())
fig,axs=plt.subplots(2,1,figsize=(11,7),sharex=True,gridspec_kw={'height_ratios':[3,1]})
curve=pd.read_csv(Path(full['report'])/'marked_daily_equity.csv',index_col=0,parse_dates=True)
axs[0].plot(curve.index,curve.equity,color='#176b87',label='Equity after fees, marked to market')
axs[0].axhline(500,color='gray',lw=.7)
axs[0].axvspan(pd.Timestamp('2025-01-01',tz='UTC'),curve.index[-1],color='#77bb88',alpha=.15,label='Held-out period')
axs[0].set_ylabel('USDT');axs[0].legend(loc='upper left');axs[0].set_title('ETHUSDT downtrend pullback short | fixed risk 0.5% per trade')
axs[1].fill_between(curve.index,curve.drawdown_pct,0,color='#ba4b4b',alpha=.6)
axs[1].set_ylabel('Drawdown %')
for ax in axs:ax.grid(alpha=.15)
fig.tight_layout();fig.savefig(ROOT/'equity.png',dpi=160);plt.close(fig)

def row(label,s):return f"| {label} | {s['end_balance']:.2f} | +{s['net_pnl']:.2f} | +{s['return_pct']:.2f}% | {s['trades']} | {s['profit_factor']:.2f} | {s['minute_close_max_drawdown_pct']:.2f}% |"
annual='\n'.join(f"| {y} | {v:+.2f} |" for y,v in full['annual_closed_pnl'].items())
relative=lambda p:'../../'+str(Path(p).relative_to('strategy_python/_report'))
text=f'''# 下跌趋势反弹做空：实现与验证

已新增独立策略 `pullback_short_1h`，并在 NautilusTrader 原生回测中验证正收益。
收益较温和，2021 和 2023 年亏损；它仍是待组合验证的低频候选，不能据此宣称可以改善原三策略组合或保证未来盈利。

## 固定规则

前一根完整日线低点跌破 BOLL(20,2) 下轨且收盘低于中轨；上一小时收盘位于
SMA20 上方或等于均线，本小时收盘重新跌到 SMA20 下方时市价做空。
止损为近 12 小时最高价 + 0.25×ATR14，距离须在 0.4%～3%；止盈 4R，
满 24 小时市价退出，单笔风险预算 0.5%，名义金额上限为权益 1 倍，不加仓。

[完整交易规则](../../../pullback_short_1h/README.md) · [固定参数](selection.json)

## 原生回测结果

ETHUSDT 永续；每次独立回测初始 500 USDT；1 分钟行情，内部小时/日线聚合；
手续费按 Catalog 合约定义实际扣除。区间结束为 2026-09-01 零点，不包含该时刻。

| 区间 | 期末 USDT | 净收益 USDT | 收益率 | 笔数 | 盈利因子 | 分钟收盘权益最大回撤 |
|---|---:|---:|---:|---:|---:|---:|
{row('2021-01-01～2026-08-31 全历史',full)}
{row('2025-01-01～2026-08-31 保留区间',hold)}

全历史胜率 {full['win_rate_pct']:.2f}%，手续费 {full['fees']:.2f} USDT，最大持仓 {full['max_hold_hours']:.0f} 小时。
回撤按成交现金流与每分钟收盘价重建，包含持仓浮盈亏；不是原报告仅计已实现盈亏的回撤，
也不是分钟内部最不利价格的权益回撤。图按每日最后一分钟采样，表中回撤按全部分钟计算。

![权益与回撤](equity.png)

| 平仓年份 | 全历史运行中的净收益 USDT |
|---|---:|
{annual}

各年贡献来自同一次复利运行，不等于各年以 500 USDT 独立重跑；2026 年只含 1～8 月。

## 成本压力

在原生手续费之上，每次成交额额外扣除滑点成本（固定成交路径的敏感性分析，不会重排成交）：

| 每次成交额额外成本 | 全历史净收益 USDT | 保留区间净收益 USDT |
|---|---:|---:|
| 0.02% | {full['extra_slippage_stress']['2']['net_pnl']:.2f} | {hold['extra_slippage_stress']['2']['net_pnl']:.2f} |
| 0.05% | {full['extra_slippage_stress']['5']['net_pnl']:.2f} | {hold['extra_slippage_stress']['5']['net_pnl']:.2f} |
| 0.10% | {full['extra_slippage_stress']['10']['net_pnl']:.2f} | {hold['extra_slippage_stress']['10']['net_pnl']:.2f} |

原生回测没有重放历史资金费率。额外假设空仓每 8 小时都支付当时名义金额的 0.01%，
并叠加每次成交 0.02% 滑点后，全历史仍余 {full['2bp_slippage_plus_assumed_funding_net_pnl']:.2f} USDT，
保留区间余 {hold['2bp_slippage_plus_assumed_funding_net_pnl']:.2f} USDT；这只是成本敏感性，不是实际资金费率结果。

## 优化过程与边界

- 训练：2021–2023；验证筛选：2024；参数固定后才评价 2025–2026 年 8 月。
- 共筛选 294 组。第一轮五分钟反弹阴线策略 36 组均未在训练/验证同时盈利；
  第二轮较慢周期 162 组也未通过；第三轮均线反弹失败结构 96 组出现候选。
- 选择小时 SMA20、24 小时退出、4R，而非训练收益最高的 72 小时版本，优先限制持仓时间。
  同一结构 2.5R、6R 在训练/验证也均盈利；排除只含约 12 笔训练交易的 SMA99 候选。
- 筛选器使用次分钟开盘、双边各 0.05% 手续费与各 0.02% 不利滑点，
  同一分钟止损/止盈都可能触发时先按止损处理。最终策略在确认小时收盘后市价入场，
  采用真实成交均价及原生止损/限价止盈；因此筛选器不是最终业绩来源。
  筛选全期 133 笔、约 +70.93 USDT；原生 132 笔、+{full['net_pnl']:.2f} USDT。
- 数据来自现有本地 Catalog，全历史有 {full['data_gap_count']} 处缺口、共 {full['missing_minutes']} 根缺失分钟；
  保留区间有 {hold['missing_minutes']} 根缺失分钟。未伪造补齐，缺口处的指标重建有测试。
- 样本量有限，294 次筛选存在选择偏差。保留区间为本次实验没有用于选参的区间，
  不代表项目此前从未看过这些历史行情。
- 已注册到组合工厂，当前只支持 `risk.mode=none`；尚未验证四策略共用账户后的边际收益。

## 核验与产物

策略测试集 669 项通过；其后新增组合注册校验也通过，新策略自身共 16 项测试通过。
覆盖止损、跳空、止盈、限时退出、保护失效、数据缺口、日线过期、风险定仓、非法参数和组合注册。
静态 Ruff 检查通过。逐笔入场另由批量指标独立核验，并将全部成交净现金流核对到最终账户余额。

- [全历史交互报告]({relative(full['report'])}/report.html)、[全历史账目与权益审计]({relative(full['report'])}/audit.json)
- [保留区间交互报告]({relative(hold['report'])}/report.html)、[保留区间审计]({relative(hold['report'])}/audit.json)
- [训练/验证筛选一](screen.csv)、[筛选二](screen_slow.csv)、[筛选三](screen_failure.csv)
- [筛选器保留区间结果](selected_screen_results.json)、[逐笔信号审计](signal_audit.json)
- [来源文件哈希](source_hashes.json)

复现最终原生全期回测：

```bash
.venv/bin/python -m strategy_python.pullback_short_1h.backtest --start 2021-01-01 --end 2026-09-01
```
'''
(ROOT/'结果说明.md').write_text(text)
files=list(Path('strategy_python/pullback_short_1h').glob('*.py'))+[Path('strategy_python/portfolio.py')]
(ROOT/'source_hashes.json').write_text(json.dumps({str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in files},indent=2)+'\n')
print(ROOT/'结果说明.md')
