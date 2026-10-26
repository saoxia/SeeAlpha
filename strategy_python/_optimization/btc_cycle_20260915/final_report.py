"""Publish eight-slot comparisons only after native final-package verification."""

import hashlib
import json
import os
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parents[2]


def main():
    profiles = json.loads((ROOT / "FINAL_STRATEGIES.json").read_text())
    verification = json.loads((ROOT / "FINAL_VERIFICATION.json").read_text())
    slots = json.loads((ROOT / "slots.json").read_text())
    assert len(profiles) == len(verification["runs"]) == 8
    assert {p["slot"] for p in profiles} == set(slots)
    assert {p["slot"] for p in verification["runs"]} == set(slots)
    for result in verification["runs"]:
        assert all(
            result[k] for k in ("config_exact", "full_history_fills_exact", "daily_equity_exact")
        )
    frozen = json.loads((ROOT / "frozen_final_runtime.json").read_text())
    for path, digest in frozen.items():
        assert hashlib.sha256((PROJECT / path).read_bytes()).hexdigest() == digest, path
    pool = pd.read_csv(ROOT / "full_cycle_comparison.csv")
    annual_pool = pd.read_csv(ROOT / "full_cycle_annual.csv")
    assert len(pool) == 18
    rows, years, curves = [], [], {}
    for p in profiles:
        selected = pool[(pool.slot == p["source_slot"]) & (pool.variant == p["variant"])]
        baseline = pool[(pool.slot == p["source_slot"]) & (pool.variant == "next_base")]
        assert len(selected) == len(baseline) == 1
        actual = ROOT / "final_runs/full" / p["slot"]
        m = json.loads((actual / "metrics.json").read_text())
        assert abs(m["net_pnl"] - selected.iloc[0].net_pnl) < 1e-7
        for label, frame, variant in [
            ("优化前", baseline, "next_base"),
            ("优化后", selected, p["variant"]),
        ]:
            row = frame.iloc[0].to_dict()
            row.update(slot=p["slot"], stage=label, source_slot=p["source_slot"])
            rows.append(row)
            annual = annual_pool[
                (annual_pool.slot == p["source_slot"]) & (annual_pool.variant == variant)
            ].copy()
            assert len(annual) == 6
            annual["slot"], annual["stage"] = p["slot"], label
            years.extend(annual.to_dict("records"))
            directory = (
                actual
                if label == "优化后"
                else (ROOT / "runs/full" / (p["source_slot"] + "__next_base"))
            )
            curves[p["slot"], label] = pd.read_csv(
                directory / "daily_equity.csv", index_col=0, parse_dates=True
            ).equity
    pd.DataFrame(rows).to_csv(ROOT / "final_comparison.csv", index=False)
    pd.DataFrame(years).to_csv(ROOT / "final_annual.csv", index=False)
    lines = [
        "# BTC 八个新版本：2021 年起连续回测",
        "",
        "区间为 2021-01-01 至 2026-08-30（UTC）。各策略独立以 500 USDT 起步，账户与持仓跨年连续，手续费已计入。",
        "八个独立新包已逐一完成全区间原生回测，并与所选研究版本逐笔成交、每日权益、配置及关键指标核对一致。旧文件哈希复核通过。",
        "",
        "| 策略位置 | 版本 | 净收益 USDT | 收益率 | 最大回撤 | 胜率 | 盈亏比 | 交易数 |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for r in rows:
        lines.append(
            f"| {r['slot']} | {r['stage']} | {r['net_pnl']:.2f} | "
            f"{r['return_pct']:.2f}% | {r['max_drawdown_pct']:.2f}% | "
            f"{r['cashflow_win_rate_pct']:.2f}% | {r['payoff_ratio']:.4f} | "
            f"{int(r['cashflow_trades'])} |"
        )
    lines += ["", "## 选择理由与参数变化", ""]
    for p in profiles:
        directory = PROJECT / p["package"].replace(".", "/")
        lines += [
            (f"- **{p['slot']}**：{p['reason']} "
            f"[新版本]({directory / 'README.md'}) · "
            f"[参数变化]({directory / 'profile.json'})")
        ]
    lines += [
        "",
        "## 解释边界",
        "",
        "[总、子、时、空研究过程与各轮完整证据](CYCLE_RESEARCH.md)",
        "",
        ("风险预算下降产生的少亏不能解释为入场逻辑已获得正期望。"
        "表中保留所有亏损、交易活跃性及成本敏感性；八个位置中的部分基线相同，不能视为八份独立统计证据。"),
        ("2021—2023 包含机制系数训练数据，后续历史也已经参与多轮比较与选择。"
        "本结果不能排除过拟合，也不是盲样本验证。原先通过反复试验选出的数值，事后解释不能替代统计来源证据。"),
        ("最大回撤使用分钟盯市，图为日末权益。年度收益来自连续账户，并非逐年重置本金。"
        "未计资金费率、真实滑点与逐日历史合约规则；额外 2/5bp 成本使用固定成交路径扣费，没有重新计算持仓。"
        "原始分钟数据有 938 分钟缺口，未伪造补价。"),
        "",
        ("[完整指标、活跃性、费用及额外成本](final_comparison.csv) · "
        "[逐年收益](final_annual.csv) · [18 项候选全记录](FULL_CYCLE_RESULTS.md) · "
        "[八包验证记录](FINAL_VERIFICATION.json)"),
        "",
        "![优化前后连续 PnL 曲线](final_curves.png)",
    ]
    os.environ.setdefault("MPLCONFIGDIR", str(PROJECT / ".local/cache/matplotlib"))
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(4, 2, figsize=(15, 14), layout="constrained")
    for ax, p in zip(axes.flat, profiles, strict=True):
        for stage, color, label in [("优化前", "black", "Before"), ("优化后", "tab:blue", "After")]:
            equity = curves[p["slot"], stage]
            ax.plot(equity.index, equity - 500, color=color, label=label, lw=1)
        ax.set_title(p["slot"])
        ax.set_ylabel("Net PnL (USDT)")
        ax.axhline(0, color="gray", lw=0.5)
        ax.grid(alpha=0.2)
        ax.legend()
    fig.savefig(ROOT / "final_curves.png", dpi=140)
    plt.close(fig)
    (ROOT / "FINAL_RESULTS.md").write_text("\n".join(lines) + "\n")
    print("FINAL_REPORT_COMPLETE", len(profiles))


if __name__ == "__main__":
    main()
