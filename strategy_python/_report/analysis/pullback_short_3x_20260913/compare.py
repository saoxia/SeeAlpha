"""Compare audited native runs, verify unchanged signals, render PnL curves."""
import hashlib
import json
import os
from pathlib import Path

os.environ.setdefault('MPLCONFIGDIR','/tmp/pullback-matplotlib')
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

ROOT=Path(__file__).resolve().parent
BASE=ROOT.parent/'pullback_short_20260913'


def main():
    results={}
    for label in ('full','holdout'):
        base=json.loads((BASE/f'native_{label}_audit.json').read_text())
        trial=json.loads((ROOT/f'{label}_audit.json').read_text())
        p=Path(trial['report']);params=json.loads((p/'parameters.json').read_text())
        assert float(params['backtest_spec']['leverage'])==3
        assert params['strategy_config']['risk_fraction']==.015
        assert params['strategy_config']['max_notional_fraction']==3
        old=pd.read_csv(Path(base['report'])/'fills.csv')
        new=pd.read_csv(p/'fills.csv')
        signals_equal=old[['ts_last','side','avg_px']].reset_index(drop=True).equals(new[['ts_last','side','avg_px']].reset_index(drop=True))
        assert signals_equal,'Investigate changed trade path before claiming sizing-only comparison'
        oldpos=pd.read_csv(Path(base['report'])/'positions.csv')
        newpos=pd.read_csv(p/'positions.csv')
        # Notional/equity at each entry, independently reconstructed from closed PnL.
        pnls=newpos.realized_pnl.str.split().str[0].astype(float)
        entry_equity=500+pnls.cumsum().shift(fill_value=0)
        exposures=newpos.peak_qty*newpos.avg_px_open/entry_equity
        result={'baseline':base,'three_x':trial,'identical_fill_times_sides_prices':signals_equal,
                'entry_notional_to_equity_min':float(exposures.min()),
                'entry_notional_to_equity_median':float(exposures.median()),
                'entry_notional_to_equity_max':float(exposures.max()),
                'first_quantity_ratio':float(newpos.peak_qty.iloc[0]/oldpos.peak_qty.iloc[0])}
        results[label]=result
    fig,axs=plt.subplots(2,1,figsize=(11,7),sharex=True,gridspec_kw={'height_ratios':[3,1]})
    for key,color,name in [('baseline','#176b87','Baseline (0.5% risk/trade)'),('three_x','#d27524','3x sizing (1.5% risk/trade)')]:
        a=results['full'][key];curve=pd.read_csv(Path(a['report'])/'marked_daily_equity.csv',index_col=0,parse_dates=True)
        axs[0].plot(curve.index,curve.equity-500,lw=1.6,color=color,label=f'{name}: +{a["net_pnl"]:.2f} USDT')
        axs[1].plot(curve.index,curve.drawdown_pct,lw=1.2,color=color)
    axs[0].axhline(0,color='gray',lw=.8)
    axs[0].axvspan(pd.Timestamp('2025-01-01',tz='UTC'),curve.index[-1],color='#77bb88',alpha=.12)
    axs[0].set_title('ETHUSDT pullback short | 500 USDT initial balance | after fees')
    axs[0].set_ylabel('Cumulative PnL (USDT, marked to market)');axs[0].legend(loc='upper left')
    axs[1].set_ylabel('Drawdown %')
    for ax in axs:ax.grid(alpha=.15)
    fig.tight_layout();fig.savefig(ROOT/'pnl_comparison.png',dpi=160);plt.close(fig)
    (ROOT/'comparison.json').write_text(json.dumps(results,indent=2)+'\n')
    def row(label,a):return f'| {label} | {a["end_balance"]:.2f} | {a["net_pnl"]:+.2f} | {a["return_pct"]:.2f}% | {a["minute_close_max_drawdown_pct"]:.2f}% | {a["trades"]} |'
    f=results['full'];h=results['holdout'];t=f['three_x']
    rows='\n'.join([row('全期：原仓位',f['baseline']),row('全期：3倍仓位',t),row('保留区间：原仓位',h['baseline']),row('保留区间：3倍仓位',h['three_x'])])
    annual='\n'.join(f'| {year} | {f["baseline"]["annual_closed_pnl"][year]:+.2f} | {pnl:+.2f} |' for year,pnl in t['annual_closed_pnl'].items())
    text=f'''# 反弹做空：3倍仓位对照

此处的“3倍”指单笔风险从权益0.5%升到1.5%，名义仓位上限从权益1倍升到3倍。
同时将本次试验账户保证金杠杆设为3倍。原基线账户杠杆是5倍，但实际仓位由风险定仓，
所以只把保证金杠杆改成3倍通常不会放大仓位或收益。
这是独立试验，未修改策略默认参数。

## 实际回测

同一ETHUSDT行情、500 USDT起始资金、相同信号和退出规则，重新运行Nautilus原生回测。
全期2021-01-01至2026-09-01；保留区间2025-01-01至2026-09-01，结束时间不包含。

| 区间/仓位 | 期末USDT | 净收益USDT | 累计收益率 | 分钟收盘权益最大回撤 | 笔数 |
|---|---:|---:|---:|---:|---:|
{rows}

两次试验全部成交时间、方向、成交价格与各自基线一致，变化来自仓位、手续费及复利。
所有净现金流都已核对到最终账户余额。回撤包含浮盈亏，不是分钟内部最不利价格回撤。

![PnL对照](pnl_comparison.png)

全期3倍版本盈利因子{t['profit_factor']:.2f}，手续费{t['fees']:.2f} USDT。
实际入场名义金额/权益的中位数{f['entry_notional_to_equity_median']:.2f}倍，
范围{f['entry_notional_to_equity_min']:.2f}～{f['entry_notional_to_equity_max']:.2f}倍。
因此“放大原仓位3倍”不等于每笔都固定持有权益3倍的名义仓位；数量取整和复利也使后续数量比不恒定为3。

| 年份 | 原仓位净收益USDT | 3倍仓位净收益USDT |
|---|---:|---:|
{annual}

2026年只含1～8月。上表按全期运行中持仓平仓年份统计，非逐年独立500 USDT回测。

## 成本及数据口径

原生手续费已扣除。每次成交额再额外扣除0.1%滑点成本后，全期仍余
{t['extra_slippage_stress']['10']['net_pnl']:.2f} USDT，保留区间余
{h['three_x']['extra_slippage_stress']['10']['net_pnl']:.2f} USDT。
这是固定成交路径成本敏感性，不是改变撮合的重跑；真实资金费率未重放。
沿用本地数据缺口处理，全期有{t['missing_minutes']}根缺失分钟。
回测未核验交易所实际维持保证金阶梯和强平流程，不能据此作无强平保证。

## 复现

```bash
.venv/bin/python -u -m strategy_python._report.analysis.pullback_short_3x_20260913.run_trial full
.venv/bin/python -u -m strategy_python._report.analysis.pullback_short_3x_20260913.run_trial holdout
.venv/bin/python -m strategy_python._report.analysis.pullback_short_3x_20260913.compare
```

[完整数值与原生报告路径](comparison.json) · [全期审计](full_audit.json) · [保留区间审计](holdout_audit.json)
'''
    (ROOT/'结果说明.md').write_text(text)
    files=list(Path('strategy_python/pullback_short_1h').glob('*.py'))+[ROOT/'run_trial.py',ROOT/'compare.py']
    (ROOT/'source_hashes.json').write_text(json.dumps({str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in files},indent=2)+'\n')
    print(rows,flush=True)


if __name__=='__main__':main()
