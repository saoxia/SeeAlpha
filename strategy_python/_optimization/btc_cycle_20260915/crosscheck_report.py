"""Report all shortlisted candidates across two independent-start historical windows."""

import argparse
import ast
import hashlib
import json
import os
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parents[2]


def trade_shape(path):
    f = pd.read_csv(path / "fills.csv")
    if f.empty:
        return {
            "active_months": 0,
            "peak_abs_btc_at_fill": 0.0,
            "peak_notional_at_fill": 0.0,
            "peak_gross_leverage_at_fill": 0.0,
            "nonpositive_equity_fill_count": 0,
            "first_fill": None,
            "last_fill": None,
            "active_years": "",
            "unit_net_mean": None,
            "unit_payoff": None,
            "net_without_best_five": 0,
        }
    f["time"] = pd.to_datetime(f.ts_last, utc=True)
    f["notional"] = f.filled_qty.astype(float) * f.avg_px.astype(float)
    fee = f.commissions.map(lambda s: sum(float(x.split()[0]) for x in ast.literal_eval(s)))
    f["flow"] = np.where(f.side == "SELL", 1, -1) * f.notional - fee
    # Observe exposure immediately after each fill; this is not a minute-by-minute peak.
    ordered = f.sort_values("time", kind="stable")
    signed_qty = np.where(ordered.side == "BUY", 1, -1) * ordered.filled_qty.astype(float)
    qty = pd.Series(signed_qty, index=ordered.index).cumsum().round(12)
    notional = qty.abs() * ordered.avg_px.astype(float)
    equity = 500 + ordered.flow.cumsum() + qty * ordered.avg_px.astype(float)
    leverage = notional / equity.where(equity > 0)
    rows = []
    for _, g in f.groupby("position_id", sort=False):
        g = g.sort_values("time", kind="stable")
        denom = g.loc[g.side == g.side.iloc[0], "notional"].sum()
        rows.append((g.flow.sum(), g.flow.sum() / denom))
    t = pd.DataFrame(rows, columns=["pnl", "unit"])
    win = t[t.pnl > 0].unit
    loss = t[t.pnl < 0].unit
    return {
        "active_months": f.time.dt.strftime("%Y-%m").nunique(),
        "peak_abs_btc_at_fill": float(qty.abs().max()),
        "peak_notional_at_fill": float(notional.max()),
        "peak_gross_leverage_at_fill": float(leverage.max()) if leverage.notna().any() else None,
        "nonpositive_equity_fill_count": int((equity <= 0).sum()),
        "first_fill": f.time.min().isoformat(),
        "last_fill": f.time.max().isoformat(),
        "active_years": ",".join(str(y) for y in sorted(f.time.dt.year.unique())),
        "unit_net_mean": t.unit.mean(),
        "unit_payoff": win.mean() / -loss.mean() if len(win) and len(loss) else None,
        "net_without_best_five": t.pnl.sum() - t.pnl.nlargest(min(5, len(t))).sum(),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--next", action="store_true", dest="next_round")
    args = parser.parse_args()
    prefix = "next_" if args.next_round else ""
    baseline = "next_base" if args.next_round else "main"
    runner = "next_run" if args.next_round else "run"
    cases = json.loads((ROOT / (prefix + "crosscheck_cases.json")).read_text())
    experiments = json.loads((ROOT / (prefix + "experiments.json")).read_text())
    shortlist = json.loads(
        (ROOT / ("next_shortlist.json" if args.next_round else "round1_shortlist.json")).read_text()
    )
    frozen = json.loads(
        (
            ROOT / ("frozen_next_runtime.json" if args.next_round else "frozen_runtime.json")
        ).read_text()
    )
    for source, digest in frozen.items():
        assert hashlib.sha256((PROJECT / source).read_bytes()).hexdigest() == digest, source
    rows = []
    lookup = {}
    for module, slot, period, variant in cases:
        assert module == runner
        p = ROOT / "runs" / period / (slot + ("__" + variant if variant != "main" else ""))
        m = json.loads((p / "metrics.json").read_text())
        params = json.loads((p / "parameters.json").read_text())
        assert (
            params["runner_sha256"] == frozen[str((ROOT / (runner + ".py")).relative_to(PROJECT))]
        )
        for source, digest in params["source_sha256"].items():
            assert hashlib.sha256((PROJECT / source).read_bytes()).hexdigest() == digest, source
        assert (
            m["off_tick_order_price_fields"] == 0
            and abs(m.get("cashflow_reconciliation_error", 0)) < 1e-4
        )
        row = {
            "slot": slot,
            "period": period,
            "variant": variant,
            **{
                k: m.get(k)
                for k in [
                    "net_pnl",
                    "return_pct",
                    "max_drawdown_pct",
                    "cashflow_trades",
                    "cashflow_win_rate_pct",
                    "payoff_ratio",
                    "fees",
                ]
            },
            "extra_5bp_net_pnl": m["stress"]["5"]["net_pnl"],
            **trade_shape(p),
        }
        rows.append(row)
        lookup[slot, period, variant] = row
    decisions = []
    for item in shortlist:
        checks = []
        for period in ["validation", "review"]:
            base = lookup[item["slot"], period, baseline]
            candidate = lookup[item["slot"], period, item["variant"]]
            minutes = experiments[item["slot"]][item["variant"]]["overrides"].get(
                "cycle_signal_minutes", 5
            )
            activity = candidate["cashflow_trades"] >= max(
                10, 0.25 * base["cashflow_trades"] * 5 / minutes
            )
            dominates = (
                candidate["net_pnl"] > base["net_pnl"]
                and candidate["max_drawdown_pct"] <= base["max_drawdown_pct"]
                and activity
            )
            candidate["pnl_change"] = candidate["net_pnl"] - base["net_pnl"]
            candidate["dd_change"] = candidate["max_drawdown_pct"] - base["max_drawdown_pct"]
            candidate["dominates_here"] = dominates
            checks.append(dominates)
        decisions.append(
            {
                **item,
                "validation_dominates": checks[0],
                "review_dominates": checks[1],
                "cross_period_status": "both_improved" if all(checks) else "mixed_or_failed",
            }
        )
    pd.DataFrame(rows).to_csv(ROOT / (prefix + "crosscheck_comparison.csv"), index=False)
    (ROOT / (prefix + "crosscheck_decisions.json")).write_text(
        json.dumps(decisions, indent=2) + "\n"
    )
    lines = [
        "# " + ("第二轮" if args.next_round else "第一轮") + "：跨时间复核",
        "",
        "所有候选均来自事先固定开发筛选规则。2024和2025以来分别独立500 USDT。后段行情此前已经查看，不称盲样本外；同区间风险预算及费用一致。这里不是最终8策略交付结论。",
        "",
        "| 位置 | 方案 | 区间 | 净收益 | 回撤 | 胜率 | 盈亏比 | 成交数 | 活跃月数 | 额外每侧5bp后 |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in rows:
        payoff = r["payoff_ratio"] if r["payoff_ratio"] is not None else float("nan")
        lines.append(
            f"| {r['slot']} | {r['variant']} | {r['period']} | {r['net_pnl']:.2f} | {r['max_drawdown_pct']:.2f}% | {r['cashflow_win_rate_pct']:.1f}% | {payoff:.3f} | {r['cashflow_trades']} | {r['active_months']} | {r['extra_5bp_net_pnl']:.2f} |"
        )
    lines += [
        "",
        "## 逐项判定",
        "",
        "| 位置/方案 | 开发期 | 2024同时改善 | 近期同时改善 |",
        "|---|---|---|---|",
    ]
    for r in decisions:
        lines.append(
            f"| {r['slot']}/{r['variant']} | {r['status']} | {r['validation_dominates']} | {r['review_dominates']} |"
        )
    lines += [
        "",
        "同时改善要求收益更高、回撤不高且交易数不少于10和按信号周期折算后基线25%的较大者；这是审慎的描述性门槛，不是统计显著性。成本、单位名义收益、活跃性等完整指标见[CSV](crosscheck_comparison.csv)。去掉最好五笔仅为集中度描述，不是可交易策略。未通过者返回假设诊断，不用风险缩放掩盖机制失败。",
        "",
        "![独立区间资金曲线](crosscheck_curves.png)",
    ]
    (ROOT / (prefix.upper() + "CROSSCHECK_RESULTS.md")).write_text(
        "\n".join(lines).replace("(crosscheck_", "(" + prefix + "crosscheck_") + "\n"
    )
    os.environ.setdefault("MPLCONFIGDIR", str(PROJECT / ".local/cache/matplotlib"))
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    slots = list(dict.fromkeys(r["slot"] for r in shortlist))
    if slots:
        fig, axes = plt.subplots(
            len(slots), 2, figsize=(12, 2.8 * len(slots)), squeeze=False, layout="constrained"
        )
        for i, slot in enumerate(slots):
            variants = [baseline] + [r["variant"] for r in shortlist if r["slot"] == slot]
            for j, period in enumerate(["validation", "review"]):
                for variant in variants:
                    p = (
                        ROOT
                        / "runs"
                        / period
                        / (slot + ("__" + variant if variant != "main" else ""))
                    )
                    d = pd.read_csv(p / "daily_equity.csv", index_col=0, parse_dates=True)
                    axes[i, j].plot(d.index, d.equity - 500, label=variant, lw=1)
                axes[i, j].set_title(slot + " " + period)
                axes[i, j].set_ylabel("Net PnL (USDT)")
                axes[i, j].legend(fontsize=7)
                axes[i, j].grid(alpha=0.2)
                axes[i, j].tick_params(axis="x", rotation=15)
        fig.savefig(ROOT / (prefix + "crosscheck_curves.png"), dpi=130)
        plt.close(fig)
    print("CROSSCHECK_REPORT_COMPLETE", len(rows))


if __name__ == "__main__":
    main()
