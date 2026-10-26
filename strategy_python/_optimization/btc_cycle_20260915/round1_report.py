"""Complete first-round table and predeclared bounded shortlist, not final acceptance."""

import hashlib
import json
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parents[2]


def main():
    cases = json.loads((ROOT / "round1_cases.json").read_text())
    frozen = json.loads((ROOT / "frozen_runtime.json").read_text())
    for p, digest in frozen.items():
        assert hashlib.sha256((PROJECT / p).read_bytes()).hexdigest() == digest, p
    experiments = json.loads((ROOT / "experiments.json").read_text())
    rows = []
    for slot, period, variant in cases:
        path = ROOT / "runs" / period / (slot + ("__" + variant if variant != "main" else ""))
        m = json.loads((path / "metrics.json").read_text())
        p = json.loads((path / "parameters.json").read_text())
        assert p["start"] == "2021-01-01T00:00:00+00:00" and p["end"] == "2024-01-01T00:00:00+00:00"
        assert p["runner_sha256"] == frozen[str((ROOT / "run.py").relative_to(PROJECT))]
        assert (
            m["off_tick_order_price_fields"] == 0
            and abs(m.get("cashflow_reconciliation_error", 0)) < 1e-4
        )
        for source, digest in p["source_sha256"].items():
            assert hashlib.sha256((PROJECT / source).read_bytes()).hexdigest() == digest, source
        rows.append(
            {
                "slot": slot,
                "variant": variant,
                "period": period,
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
                "extra_5bp_net_pnl": m["stress"]["5"]["net_pnl"],
                "annual": m["annual_marked_pnl"],
            }
        )
    f = pd.DataFrame(rows)
    selected = []
    for slot, g in f.groupby("slot", sort=False):
        b = g[g.variant == "main"].iloc[0]
        for idx, row in g.iterrows():
            minutes = experiments[slot][row.variant]["overrides"].get("cycle_signal_minutes", 5)
            enough = row.cashflow_trades >= max(15, 0.25 * b.cashflow_trades * 5 / minutes)
            both = row.net_pnl > b.net_pnl and row.max_drawdown_pct <= b.max_drawdown_pct and enough
            f.loc[idx, "development_dominates"] = bool(both)
            f.loc[idx, "activity_pass"] = bool(enough)
            f.loc[idx, "pnl_change"] = row.net_pnl - b.net_pnl
            f.loc[idx, "dd_change"] = row.max_drawdown_pct - b.max_drawdown_pct
            f.loc[idx, "selection_score"] = (row.net_pnl - b.net_pnl) / 500 - (
                row.max_drawdown_pct - b.max_drawdown_pct
            ) / 100
        candidates = f[(f.slot == slot) & (f.variant != "main") & (f.activity_pass == True)]
        passed = candidates[candidates.development_dominates == True]
        chosen = (
            passed
            if len(passed)
            else candidates[
                (candidates.dd_change < 0) | (candidates.extra_5bp_net_pnl > b.extra_5bp_net_pnl)
            ]
        )
        mechanism_seen = set()
        count = 0
        for _, row in chosen.sort_values("selection_score", ascending=False).iterrows():
            mechanism = "".join(c for c in row.variant if not c.isdigit())
            if mechanism in mechanism_seen:
                continue
            selected.append(
                {
                    "slot": slot,
                    "variant": row.variant,
                    "status": "development_pass"
                    if bool(row.development_dominates)
                    else "diagnostic_only",
                    "mechanism": mechanism,
                }
            )
            mechanism_seen.add(mechanism)
            count += 1
            if count >= (2 if len(passed) else 1):
                break
    f.drop(columns=["annual"]).to_csv(ROOT / "round1_comparison.csv", index=False)
    (ROOT / "round1_results.json").write_text(json.dumps(rows, indent=2) + "\n")
    (ROOT / "round1_shortlist.json").write_text(json.dumps(selected, indent=2) + "\n")
    lines = [
        "# 第一轮开发区间：全部候选",
        "",
        "这不是最终接受报告。2021—2023与机制训练重叠，所有入选者还需2024/近期、后续组合、时间/仓位复核。筛选规则见ROUND1_SELECTION.md。",
        "",
        "| 位置 | 变更 | 净收益 | 回撤 | 胜率 | 盈亏比 | 相对基线收益变化 | 回撤变化百分点 | 开发期同时改善 |",
        "|---|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for _, r in f.iterrows():
        lines.append(
            f"| {r.slot} | {r.variant} | {r.net_pnl:.2f} | {r.max_drawdown_pct:.2f}% | {r.cashflow_win_rate_pct:.1f}% | {r.payoff_ratio:.4f} | {r.pnl_change:.2f} | {r.dd_change:.2f} | {bool(r.development_dominates)} |"
        )
    lines += [
        "",
        "[完整数值](round1_comparison.csv) · [进入下一轮复核的候选](round1_shortlist.json)。没有通过的家族仍需继续优化，diagnostic_only不得视为接受。",
    ]
    (ROOT / "ROUND1_RESULTS.md").write_text("\n".join(lines) + "\n")
    print("ROUND1_REPORT_COMPLETE", len(rows), "SHORTLIST", len(selected))


if __name__ == "__main__":
    main()
