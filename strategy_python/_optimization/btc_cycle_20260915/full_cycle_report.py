"""Verified complete continuous replay, including the declared 40-minute neighbor."""

import hashlib
import json
import os
from pathlib import Path

import pandas as pd

from .crosscheck_report import trade_shape

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parents[2]
METRICS = [
    "net_pnl",
    "return_pct",
    "max_drawdown_pct",
    "cashflow_trades",
    "cashflow_win_rate_pct",
    "payoff_ratio",
    "fees",
    "turnover_usdt",
]


def main():
    cases = json.loads((ROOT / "full_cycle_cases.json").read_text()) + json.loads(
        (ROOT / "full_cycle_neighbor_cases.json").read_text()
    )
    assert len(cases) == 18 and len({tuple(c) for c in cases}) == 18
    frozen = json.loads((ROOT / "frozen_full_cycle.json").read_text())
    for source, digest in frozen.items():
        assert hashlib.sha256((PROJECT / source).read_bytes()).hexdigest() == digest, source
    rows, annual, curves = [], [], {}
    for module, slot, period, variant in cases:
        assert period == "full"
        p = ROOT / "runs/full" / (slot + "__" + variant)
        m = json.loads((p / "metrics.json").read_text())
        params = json.loads((p / "parameters.json").read_text())
        assert params["start"] == "2021-01-01T00:00:00+00:00"
        assert params["end"] == "2026-08-31T00:00:00+00:00"
        assert (
            params["runner_sha256"] == frozen[str((ROOT / (module + ".py")).relative_to(PROJECT))]
        )
        for source, digest in params["source_sha256"].items():
            assert hashlib.sha256((PROJECT / source).read_bytes()).hexdigest() == digest, source
        assert (
            m["off_tick_order_price_fields"] == 0 and abs(m["cashflow_reconciliation_error"]) < 1e-4
        )
        d = pd.read_csv(p / "daily_equity.csv", index_col=0, parse_dates=True)
        assert d.index.is_monotonic_increasing and not d.index.has_duplicates
        curves[slot, variant] = d.equity
        row = {
            "module": module,
            "slot": slot,
            "variant": variant,
            **{k: m[k] for k in METRICS},
            **trade_shape(p),
        }
        for bp in [2, 5]:
            row[f"extra_{bp}bp_net_pnl"] = m["stress"][str(bp)]["net_pnl"]
        row["nonpositive_equity_days"] = int((d.equity <= 0).sum())
        row["positive_daily_changes"] = int((d.equity.diff() > 0).sum())
        peak = d.equity.cummax().clip(lower=500)
        below = d.equity < peak - 1e-8
        row["days_below_previous_peak"] = int(below.sum())
        rows.append(row)
        # Calendar-year changes on this continuous account; never reset capital annually.
        prior = 500.0
        for year, group in d.groupby(d.index.year):
            closing = float(group.equity.iloc[-1])
            annual.append(
                {
                    "slot": slot,
                    "variant": variant,
                    "year": int(year),
                    "net_pnl": closing - prior,
                    "closing_equity": closing,
                }
            )
            prior = closing
        assert abs((prior - 500) - m["net_pnl"]) < 1e-4, (slot, variant, prior, m["net_pnl"])
    table = pd.DataFrame(rows)
    for slot, group in table.groupby("slot", sort=False):
        base = group[group.variant == "next_base"].iloc[0]
        for idx, r in group.iterrows():
            table.loc[idx, "pnl_change"] = r.net_pnl - base.net_pnl
            table.loc[idx, "dd_change"] = r.max_drawdown_pct - base.max_drawdown_pct
            table.loc[idx, "full_return_dd_improved"] = bool(
                r.net_pnl > base.net_pnl and r.max_drawdown_pct <= base.max_drawdown_pct
            )
    table.to_csv(ROOT / "full_cycle_comparison.csv", index=False)
    pd.DataFrame(annual).to_csv(ROOT / "full_cycle_annual.csv", index=False)
    lines = [
        "# 最新机制优化：2021年以来连续回放",
        "",
        "2021-01-01至2026-08-30 UTC，每配置独立500 USDT，账户/持仓连续。2021—2023含训练期，所有历史均已查看；这是历史比较，不是无过拟合或未来盈利证明。手续费已计入，未模拟真实滑点、资金费率与逐日历史合约规则。",
        "",
        "| 位置 | 方案 | 净收益 | 收益率 | 最大回撤 | 胜率 | 盈亏比 | 交易数 | 活跃月 | 额外5bp后 |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for _, r in table.iterrows():
        lines.append(
            f"| {r.slot} | {r.variant} | {r.net_pnl:.2f} | {r.return_pct:.2f}% | {r.max_drawdown_pct:.2f}% | {r.cashflow_win_rate_pct:.2f}% | {r.payoff_ratio:.4f} | {r.cashflow_trades} | {r.active_months} | {r.extra_5bp_net_pnl:.2f} |"
        )
    lines += [
        "",
        "最大回撤按分钟盯市；下图只展示日末权益。年度金额是同一连续账户的权益变化，不是每年重置500。额外成本为固定成交路径扣费，没有按更低权益重定仓。",
        "",
        "[完整数值与实际成交暴露](full_cycle_comparison.csv) · [逐年收益](full_cycle_annual.csv)",
        "",
        "这是18项候选完整池报告，尚不是最终8个新包验收。仅风险缩减导致少亏必须明确说明；较高收益伴随不利年份也须保留。",
        "",
        "![全部连续资金曲线](full_cycle_curves.png)",
    ]
    (ROOT / "FULL_CYCLE_RESULTS.md").write_text("\n".join(lines) + "\n")
    os.environ.setdefault("MPLCONFIGDIR", str(PROJECT / ".local/cache/matplotlib"))
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    slots = table.slot.drop_duplicates().tolist()
    fig, axes = plt.subplots(len(slots), 1, figsize=(13, 3.2 * len(slots)), layout="constrained")
    for ax, slot in zip(axes, slots, strict=True):
        for variant in table[table.slot == slot].variant:
            equity = curves[slot, variant]
            style = {"color": "black", "lw": 1.8} if variant == "next_base" else {"lw": 1}
            ax.plot(equity.index, equity - 500, label=variant, **style)
        ax.axvspan(
            pd.Timestamp("2021-01-01", tz="UTC"),
            pd.Timestamp("2024-01-01", tz="UTC"),
            color="orange",
            alpha=0.06,
        )
        ax.set_title(slot)
        ax.set_ylabel("Net PnL (USDT)")
        ax.axhline(0, color="gray", lw=0.5)
        ax.grid(alpha=0.2)
        ax.legend(fontsize=7)
    fig.savefig(ROOT / "full_cycle_curves.png", dpi=140)
    plt.close(fig)
    print("FULL_CYCLE_REPORT_COMPLETE", len(rows))


if __name__ == "__main__":
    main()
