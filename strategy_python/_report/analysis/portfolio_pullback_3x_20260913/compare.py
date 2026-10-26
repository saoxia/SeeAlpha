"""Reconcile shared-account fills and mark net exposure at every minute close."""
import ast
import json
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
BASE = ROOT.parents[1] / 'backtest' / 'portfolio'


def audit(name, prices):
    candidates = []
    for p in (BASE / name).iterdir():
        if not (p / 'summary.json').exists():
            continue
        params = json.loads((p / 'parameters.json').read_text())
        if params['start'].startswith('2021-01-01') and params['end'].startswith('2026-09-01'):
            candidates.append(p)
    p = sorted(candidates)[-1]
    params = json.loads((p / 'parameters.json').read_text())
    summary = json.loads((p / 'summary.json').read_text())
    fills = pd.read_csv(p / 'fills.csv')
    positions = pd.read_csv(p / 'positions.csv')
    assert (fills.status == 'FILLED').all()
    assert (positions.side == 'FLAT').all()
    initial = float(params['backtest_spec']['starting_balance']) if 'backtest_spec' in params else 500.
    fees = fills.commissions.map(lambda x: sum(float(v.split()[0]) for v in ast.literal_eval(x)))
    qty = np.where(fills.side == 'BUY', fills.filled_qty, -fills.filled_qty)
    events = pd.DataFrame({'qty': qty, 'cash': -qty * fills.avg_px - fees})
    events.index = pd.to_datetime(fills.ts_last, utc=True)
    events = events.groupby(level=0).sum()
    assert events.index.isin(prices.index).all(), 'Unexpected fill timestamp'
    events = events.reindex(prices.index, fill_value=0)
    exposure = events.qty.cumsum()
    equity = initial + events.cash.cumsum() + exposure * prices
    assert abs(exposure.iloc[-1]) < 1e-7
    balance = float(summary['summary']['account.BINANCE.balance.USDT.total'].split()[0])
    assert abs(equity.iloc[-1] - balance) < 1e-4
    pnl = positions.realized_pnl.str.split().str[0].astype(float)
    # Native position-level realized_pnl can differ from account cash settlement.
    # Attribute final net profit by actual fill cashflow, with every member flat.
    contribution = pd.DataFrame({'strategy': fills.strategy_id, 'qty': qty,
                                 'cash': -qty*fills.avg_px-fees}).groupby('strategy').sum()
    assert (contribution.qty.abs() < 1e-7).all()
    assert abs(contribution.cash.sum() - (balance-initial)) < 1e-4
    dd = 100 * (equity / equity.cummax().clip(lower=initial) - 1)
    annual = equity.resample('YE').last().diff()
    annual.iloc[0] = equity.resample('YE').last().iloc[0] - initial
    stats = {'report': str(p), 'initial': initial, 'final': balance,
             'net_pnl': balance-initial, 'return_pct': 100*(balance/initial-1),
             'max_drawdown_pct': -float(dd.min()), 'drawdown_trough': str(dd.idxmin()),
             'trades': len(positions), 'fees': float(fees.sum()),
             'annual_marked_pnl': {str(k.year): float(v) for k,v in annual.items()},
             'by_strategy': summary['by_strategy'],
             'by_strategy_cash_pnl': contribution.cash.to_dict(),
             'native_position_pnl_sum': float(pnl.sum()),
             'account_minus_position_pnl': float(balance-initial-pnl.sum())}
    daily = pd.DataFrame({'equity': equity, 'pnl': equity-initial, 'drawdown_pct': dd}).resample('1D').last()
    daily.to_csv(ROOT / f'{name}_daily.csv')
    return stats, daily


def main():
    from nautilus_trader.persistence.catalog import ParquetDataCatalog
    from pipeline.config import load_settings

    prices = pd.read_parquet(ROOT.parent / 'pullback_short_20260913' / 'minutes.parquet')['close']
    prices.index -= pd.Timedelta(milliseconds=1)
    # The earlier research cache excluded the last boundary at Sep 1.
    # Read the final day from the same native Catalog used in the backtest.
    bars = ParquetDataCatalog(str(load_settings().catalog_dir)).bars(
        bar_types=['ETHUSDT-PERP.BINANCE-1-MINUTE-LAST-EXTERNAL'],
        start=pd.Timestamp('2026-08-31', tz='UTC'), end=pd.Timestamp('2026-09-01', tz='UTC'))
    tail = pd.Series([float(b.close) for b in bars], index=pd.to_datetime([b.ts_event for b in bars], utc=True))
    prices = pd.concat([prices, tail])
    prices = prices[~prices.index.duplicated(keep='last')].sort_index()
    prices = prices.loc[(prices.index >= '2021-01-01') & (prices.index < '2026-09-01')]
    results = {}
    fig, axes = plt.subplots(2, 1, figsize=(12, 7), sharex=True, gridspec_kw={'height_ratios': [2, 1]})
    for name, label in [('mean_reversion_long_trend_both', 'Original 3 strategies'),
                        ('mean_reversion_long_trend_both_pullback_3x', '4 strategies: + hourly pullback short')]:
        stats, daily = audit(name, prices)
        results[name] = stats
        axes[0].plot(daily.index, daily.pnl, label=label, linewidth=1.3)
        axes[1].plot(daily.index, daily.drawdown_pct, linewidth=1.1)
    axes[0].set_title('ETHUSDT shared-account portfolio | Initial 500 USDT | After fees')
    axes[0].set_ylabel('Cumulative PnL (USDT)')
    axes[0].legend()
    axes[1].set_ylabel('Drawdown (%)')
    for ax in axes:
        ax.grid(alpha=.2)
    fig.tight_layout()
    fig.savefig(ROOT / 'pnl_comparison.png', dpi=160)
    (ROOT / 'comparison.json').write_text(json.dumps(results, indent=2)+'\n')
    old, new = results.values()
    lines = ['# 小时反弹做空加入组合：全历史对照', '',
             'ETHUSDT，2021-01-01 至 2026-09-01（结束时间不含），500 USDT 初始资金，'
             '1 分钟撮合，135 天预热，同一账户 5 倍保证金杠杆。', '',
             '新增 PBS 单笔计划风险 1.5%、名义仓位上限 3 倍；原三成员参数一致。'
             'risk.mode=none，不提供组合统一风险上限。', '',
             '| 组合 | 净收益 USDT | 累计收益率 | 权益最大回撤 | 已平仓笔数 |',
             '|---|---:|---:|---:|---:|']
    for label, r in [('原三策略', old), ('新增 PBS 四策略', new)]:
        lines.append(f"| {label} | {r['net_pnl']:.2f} | {r['return_pct']:.2f}% | {r['max_drawdown_pct']:.2f}% | {r['trades']} |")
    lines += ['', '![PnL 与回撤](pnl_comparison.png)', '',
              '回撤按每分钟收盘权益计算，包含未实现盈亏；图按日采样。'
              '成交现金流与最终账户余额已核对一致；成员贡献同样按实际成交现金流扣手续费计算。', '',
              '## 成员净收益对照', '', '| 成员 | 原三策略 USDT | 四策略 USDT |', '|---|---:|---:|']
    for key, r in new['by_strategy'].items():
        prev = old['by_strategy_cash_pnl'].get(key, 0)
        value = new['by_strategy_cash_pnl'][key]
        lines.append(f'| {key} | {prev:.2f} | {value:.2f} |')
    lines += ['', '各策略共享权益和保证金，仓位相互影响，因此收益不能用独立回测简单相加。', '',
              f"原生持仓表收益合计与账户净收益分别相差 {old['account_minus_position_pnl']:.6f} / {new['account_minus_position_pnl']:.6f} USDT（原组合/新组合）；原因尚未定位，本文以已对账的成交现金流和账户余额为准。", '',
              '## 年度权益变化', '', '| 年份 | 原三策略 USDT | 四策略 USDT |', '|---|---:|---:|']
    for year, value in new['annual_marked_pnl'].items():
        lines.append(f"| {year} | {old['annual_marked_pnl'][year]:.2f} | {value:.2f} |")
    lines += ['', '2026 年只含 1–8 月。年度变化取年末账户权益差，包含跨年持仓浮盈亏。', '',
              '已扣原生手续费，未重放真实历史资金费率，也未添加额外盘口滑点。'
              '沿用本地行情数据缺口；未完整验证交易所强平规则。这是历史回测，不是样本外盈利证明。', '',
              f"[原三策略原生报告]({old['report']}/report.html) · [四策略原生报告]({new['report']}/report.html)", '',
              '[完整数值](comparison.json)']
    (ROOT / '结果说明.md').write_text('\n'.join(lines)+'\n')
    print(json.dumps(results, indent=2))


if __name__ == '__main__':
    main()
