"""Predeclared second-round comparison; a shortlist is not final acceptance."""

import hashlib
import json
from pathlib import Path

import pandas as pd

from .crosscheck_report import trade_shape

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parents[2]


def category(variant):
    if variant.startswith("space_margin"):
        return "account_margin"
    if variant.startswith(("space_budget", "space_cap", "space_risk")):
        return "risk_allocation"
    if variant.endswith("same_unit"):
        return "layers_reduced_budget"
    if variant.startswith("space_flat"):
        return "layers_flat_risk"
    if variant.startswith("space_units"):
        return "layers_same_budget"
    if variant.startswith("next_time"):
        return "exit_rearm"
    return "slope_direction"


def main():
    cases = json.loads((ROOT / "next_develop_cases.json").read_text())
    frozen = json.loads((ROOT / "frozen_next_runtime.json").read_text())
    for source, digest in frozen.items():
        assert hashlib.sha256((PROJECT / source).read_bytes()).hexdigest() == digest, source
    rows = []
    for module, slot, period, variant in cases:
        assert module == "next_run" and period == "develop"
        path = ROOT / "runs" / period / (slot + "__" + variant)
        m = json.loads((path / "metrics.json").read_text())
        p = json.loads((path / "parameters.json").read_text())
        assert p["start"] == "2021-01-01T00:00:00+00:00"
        assert p["end"] == "2024-01-01T00:00:00+00:00"
        assert p["runner_sha256"] == frozen[str((ROOT / "next_run.py").relative_to(PROJECT))]
        for source, digest in p["source_sha256"].items():
            assert hashlib.sha256((PROJECT / source).read_bytes()).hexdigest() == digest, source
        assert m["off_tick_order_price_fields"] == 0
        assert abs(m["cashflow_reconciliation_error"]) < 1e-4
        rows.append(
            dict(
                slot=slot,
                variant=variant,
                category=category(variant),
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
                        "turnover_usdt",
                    ]
                },
                extra_5bp_net_pnl=m["stress"]["5"]["net_pnl"],
                order_event_reasons=json.dumps(m["order_event_reasons"], ensure_ascii=False),
                **trade_shape(path),
            )
        )
    f = pd.DataFrame(rows)
    selected = []
    for slot, g in f.groupby("slot", sort=False):
        b = g[g.variant == "next_base"].iloc[0]
        for idx, r in g.iterrows():
            activity = r.cashflow_trades >= max(15, 0.25 * b.cashflow_trades)
            f.loc[idx, "activity_pass"] = activity
            f.loc[idx, "pnl_change"] = r.net_pnl - b.net_pnl
            f.loc[idx, "dd_change"] = r.max_drawdown_pct - b.max_drawdown_pct
            f.loc[idx, "development_dominates"] = bool(
                activity and r.net_pnl > b.net_pnl and r.max_drawdown_pct <= b.max_drawdown_pct
            )
            f.loc[idx, "selection_score"] = (r.net_pnl - b.net_pnl) / 500 - (
                r.max_drawdown_pct - b.max_drawdown_pct
            ) / 100
        passed = f[
            (f.slot == slot) & (f.variant != "next_base") & (f.development_dominates == True)
        ]
        used = set()
        for _, r in passed.sort_values("selection_score", ascending=False).iterrows():
            if r.category in used:
                continue
            selected.append(
                {
                    "slot": slot,
                    "variant": r.variant,
                    "category": r.category,
                    "status": "development_only",
                    "mechanism_claim": False,
                }
            )
            used.add(r.category)
            if len(used) == 2:
                break
    f.to_csv(ROOT / "next_comparison.csv", index=False)
    (ROOT / "next_shortlist.json").write_text(json.dumps(selected, indent=2) + "\n")
    lines = [
        "# 第二轮机制与空间：开发期结果",
        "",
        "2021—2023含训练期。候选只获得跨期复核资格；仓位缩小导致少亏不能证明机制有效。",
        "",
        "| 位置 | 方案 | 净收益 | 最大回撤 | 交易数 | 活跃月数 | 胜率 | 收益变化 | 回撤变化 |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for _, r in f.iterrows():
        lines.append(
            f"| {r.slot} | {r.variant} | {r.net_pnl:.2f} | {r.max_drawdown_pct:.2f}% | {r.cashflow_trades} | {r.active_months} | {r.cashflow_win_rate_pct:.2f}% | {r.pnl_change:.2f} | {r.dd_change:.2f} |"
        )
    lines += [
        "",
        "完整费用、盈亏比、单位名义盈亏、拒单原因及筛选判定见 [CSV](next_comparison.csv)。候选按不同变更类别最多选两个，无通过者不强选赢家。账户杠杆结果相同则说明此区间没有改善，不宣称杠杆优化。",
    ]
    (ROOT / "NEXT_RESULTS.md").write_text("\n".join(lines) + "\n")
    print("NEXT_REPORT_COMPLETE", len(rows), "SHORTLIST", len(selected))


if __name__ == "__main__":
    main()
