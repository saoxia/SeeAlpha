"""Verify the fixed fourth-cycle cases and compare against both baseline and component."""

import hashlib
import json
from pathlib import Path

import pandas as pd

from .crosscheck_report import trade_shape

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parents[2]
KEYS = [
    "net_pnl",
    "return_pct",
    "max_drawdown_pct",
    "cashflow_trades",
    "cashflow_win_rate_pct",
    "payoff_ratio",
    "fees",
]


def component(slot):
    if slot.startswith("MRL"):
        return "space_units2_same_risk"
    return {
        "TFL_original": "space_budget0.5",
        "TFS_original": "next_slope_level",
        "PBS_original": "space_risk0.005",
    }[slot]


def main():
    frozen = json.loads((ROOT / "frozen_fourth_runtime.json").read_text())
    for p, h in frozen.items():
        assert hashlib.sha256((PROJECT / p).read_bytes()).hexdigest() == h, p
    cases = json.loads((ROOT / "fourth_cases.json").read_text())
    assert len(cases) == 9
    cache = {}

    def read(slot, period, variant, runner="next_run"):
        key = (slot, period, variant)
        if key in cache:
            return cache[key]
        p = ROOT / "runs" / period / (slot + "__" + variant)
        m = json.loads((p / "metrics.json").read_text())
        params = json.loads((p / "parameters.json").read_text())
        expected = {
            "develop": ("2021-01-01", "2024-01-01"),
            "validation": ("2024-01-01", "2025-01-01"),
            "review": ("2025-01-01", "2026-08-31"),
        }[period]
        assert params["start"] == expected[0] + "T00:00:00+00:00"
        assert params["end"] == expected[1] + "T00:00:00+00:00"
        assert (
            params["runner_sha256"] == frozen[str((ROOT / (runner + ".py")).relative_to(PROJECT))]
        )
        for source, digest in params["source_sha256"].items():
            assert hashlib.sha256((PROJECT / source).read_bytes()).hexdigest() == digest, source
        assert (
            m["off_tick_order_price_fields"] == 0 and abs(m["cashflow_reconciliation_error"]) < 1e-4
        )
        row = {k: m[k] for k in KEYS}
        row.update(trade_shape(p))
        row["extra_5bp_net_pnl"] = m["stress"]["5"]["net_pnl"]
        cache[key] = row
        return row

    rows = []
    for runner, slot, period, variant in cases:
        row = dict(read(slot, period, variant, runner))
        base = read(slot, period, "next_base")
        control_name = component(slot)
        control = read(slot, period, control_name)
        row.update(slot=slot, period=period, variant=variant, component=control_name)
        row["activity_pass"] = row["cashflow_trades"] >= max(
            15 if period == "develop" else 10, 0.25 * base["cashflow_trades"]
        )
        for name, comparator in [("baseline", base), ("component", control)]:
            row[name + "_pnl_change"] = row["net_pnl"] - comparator["net_pnl"]
            row[name + "_dd_change"] = row["max_drawdown_pct"] - comparator["max_drawdown_pct"]
            row[name + "_dominates"] = bool(
                row["activity_pass"]
                and row[name + "_pnl_change"] > 0
                and row[name + "_dd_change"] <= 0
            )
        rows.append(row)
    table = pd.DataFrame(rows)
    table.to_csv(ROOT / "fourth_comparison.csv", index=False)
    decisions = []
    for (slot, variant), group in table.groupby(["slot", "variant"], sort=False):
        assert set(group.period) == {"develop", "validation", "review"}
        decisions.append(
            {
                "slot": slot,
                "variant": variant,
                "all_periods_baseline_improved": bool(group.baseline_dominates.all()),
                "all_periods_component_improved": bool(group.component_dominates.all()),
                "status": "requires_full_replay_and_neighbor_review",
            }
        )
    (ROOT / "fourth_decisions.json").write_text(json.dumps(decisions, indent=2) + "\n")
    lines = [
        "# 趋势多头时间与预算邻域",
        "",
        "这是完整9项时间与预算对照（20/30/40分钟各三个区间），并非最终策略接受结论。全部历史已看过，2021—2023与训练重叠，不称盲测。组合同时对比原基线及组成部分；仅少亏不能证明机制正期望。",
        "",
        "| 位置 | 方案 | 区间 | 净收益 | 回撤 | 胜率 | 盈亏比 | 交易数 | 活跃月 | 对基线收益变化 | 对组成部分收益变化 | 额外5bp后 |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in rows:
        payoff = r["payoff_ratio"] if r["payoff_ratio"] is not None else float("nan")
        lines.append(
            f"| {r['slot']} | {r['variant']} | {r['period']} | {r['net_pnl']:.2f} | {r['max_drawdown_pct']:.2f}% | {r['cashflow_win_rate_pct']:.2f}% | {payoff:.3f} | {r['cashflow_trades']} | {r['active_months']} | {r['baseline_pnl_change']:.2f} | {r['component_pnl_change']:.2f} | {r['extra_5bp_net_pnl']:.2f} |"
        )
    lines += [
        "",
        "组成部分为原5分钟信号、单独半预算。20/30/40分钟改变条件分布，不宣称保持5分钟训练系数的原统计覆盖。所有比较仍需关注回撤变化和实际持仓，详见[完整CSV](fourth_comparison.csv)。成本压力是固定成交路径扣费，不是重新定仓。",
        "",
        "后续须连续2021全历史、邻域与完整八位置交付审计。没有在每个时期同时改善的方案应明确说明取舍，不能隐去失败区间。",
    ]
    (ROOT / "FOURTH_RESULTS.md").write_text("\n".join(lines) + "\n")
    print("FOURTH_REPORT_COMPLETE", len(rows))


if __name__ == "__main__":
    main()
