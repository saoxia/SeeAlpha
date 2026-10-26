"""Report all hypotheses; separate declared primary tests from exploratory horizons."""

import json
import os
from pathlib import Path

import numpy as np
import pandas as pd

from . import research

ROOT = Path(__file__).resolve().parent
LABELS = {
    **research.NAMES,
    "trend_reversion_long": "顺周趋势超跌做多",
    "trend_reversion_short": "顺周趋势超涨做空",
    "failed_breakout_long": "向下假突破收复做多",
    "failed_breakout_short": "向上假突破跌回做空",
}


def adjust(frame):
    frame = frame.sort_values("p_value").copy()
    raw = frame.p_value.to_numpy() * len(frame) / np.arange(1, len(frame) + 1)
    frame["q_value"] = np.minimum.accumulate(raw[::-1])[::-1].clip(max=1)
    return frame


def main():
    table = pd.concat([pd.read_csv(p / "signal_results.csv") for p in [ROOT, ROOT / "second"]])
    events = pd.concat([pd.read_parquet(p / "events.parquet") for p in [ROOT, ROOT / "second"]])
    primary = adjust(
        pd.concat([pd.read_csv(p / "uncertainty.csv") for p in [ROOT, ROOT / "second"]])
    )
    primary.to_csv(ROOT / "joint_primary_uncertainty.csv", index=False)
    # Exploratory horizon comparison: recompute controls exactly as in the event study.
    h, _, history = research.features(pd.read_parquet(research.DATA / "BTC_60m.parquet"))
    minutes = pd.read_parquet(research.DATA / "BTC_minutes.parquet", columns=["open"])
    entry_time = h.index + pd.Timedelta(minutes=1)
    entry = minutes.open.reindex(h.index + pd.Timedelta(minutes=2)).to_numpy()
    explorations = []
    for horizon in [6, 24, 48]:
        exit_time = entry_time + pd.Timedelta(hours=horizon)
        ratio = minutes.open.reindex(exit_time + pd.Timedelta(minutes=1)).to_numpy() / entry
        continuity = (
            h.close.notna().rolling(horizon + 1).sum().shift(-(horizon + 1)).eq(horizon + 1)
        )
        valid = history & continuity & np.isfinite(ratio)
        cross_period = np.zeros(len(h), dtype=bool)
        for name in ["validation", "review"]:
            start, end = research.PERIODS[name]
            cross_period |= (entry_time >= start) & (exit_time < end)
        valid &= cross_period
        for signal in LABELS:
            side = 1 if signal.endswith("long") else -1
            controls = pd.DataFrame(
                {"entry_time": entry_time, "net": side * (ratio - 1) - 0.0005 * (1 + ratio)},
                index=h.index,
            ).loc[valid]
            ev = events[
                (events.signal == signal)
                & (events.horizon == horizon)
                & (events.period != "develop")
            ]
            explorations.append(
                {"signal": signal, "horizon": horizon, **research.bootstrap(ev, controls)}
            )
    exploratory = adjust(pd.DataFrame(explorations))
    exploratory.to_csv(ROOT / "exploratory_30_tests.csv", index=False)
    table.to_csv(ROOT / "all_results.csv", index=False)
    decisions = []
    for p in [ROOT, ROOT / "second"]:
        for d in json.loads((p / "decisions.json").read_text()):
            q = float(primary.loc[primary.signal == d["signal"], "q_value"].iloc[0])
            d["gates"]["multiple_test_pass"] = q <= 0.05
            d["q_value_ten_primary_tests"] = q
            d["accepted"] = all(d["gates"].values())
            decisions.append(d)
    (ROOT / "joint_decisions.json").write_text(json.dumps(decisions, indent=2) + "\n")
    passed = sum(d["accepted"] for d in decisions)
    lines = [
        "# BTC 入场信号研究结果",
        "",
        f"两轮共 10 个信号、3 个持有期、3 个历史阶段，90 项比较。预先声明的 24 小时主检验通过 {passed}/10 项。当前没有足够证据将这些信号称为适合 BTC 的稳定盈利入场。",
        "",
        "## 固定持有 24 小时：每笔平均扣费收益",
        "",
        "以下百分比是固定名义本金的每笔平均收益，不是账户累计收益或年化。入场/出场各扣 0.05% 吃单费用，信号后延迟一分钟。每一方案禁止持仓重叠。",
        "",
        "| 信号 | 2021—2023 | 2024 | 2025—2026.08 |",
        "|---|---:|---:|---:|",
    ]
    for signal, label in LABELS.items():
        g = table[(table.signal == signal) & (table.horizon == 24)].set_index("period")
        lines.append(
            f"| {label} | {g.loc['develop', 'net_mean']:.4%} | {g.loc['validation', 'net_mean']:.4%} | {g.loc['review', 'net_mean']:.4%} |"
        )
    candidate = table[(table.signal == "failed_breakout_long") & (table.horizon == 48)].set_index(
        "period"
    )
    ci = exploratory[
        (exploratory.signal == "failed_breakout_long") & (exploratory.horizon == 48)
    ].iloc[0]
    lines += [
        "",
        "## 保留的探索线索：向下假突破收复，持有 48 小时",
        "",
        "定义：完整小时最低价跌破此前 24 小时最低价，但该小时收盘重新站回原通道下沿；下一分钟之后按实际分钟开盘价做多，固定持有 48 小时。",
        "这不是事先声明的主持有期；是检查邻域后发现的线索，不能更换主检验来宣布通过。",
        "",
        "| 阶段 | 笔数 | 平均净收益 | 每边额外 2bp 后 | 每边额外 5bp 后 |",
        "|---|---:|---:|---:|---:|",
    ]
    for period in ["develop", "validation", "review"]:
        row = candidate.loc[period]
        lines.append(
            f"| {period} | {int(row.n)} | {row.net_mean:.4%} | {row.extra2bp_mean:.4%} | {row.extra5bp_mean:.4%} |"
        )
    lines += [
        "",
        f"2024 后平均净收益的三个月块重采样 95% 区间为 [{ci.net_ci_low:.4%}, {ci.net_ci_high:.4%}]；相对同期同方向参考入场优势区间 [{ci.excess_ci_low:.4%}, {ci.excess_ci_high:.4%}]。将 30 个信号/持有期探索一起调整的 q 值为 {ci.q_value:.4f}。",
        "",
        "## 证据边界",
        "",
        "日/周窗口与 2 倍标准差是有含义的研究假设，不是已被统计证明的最优参数。第二轮是看到第一轮后提出的探索，所有历史均已查看；块重采样、多重比较不能消除这种选择偏差。统计区间是近似评估，不是未来收益保证。",
        "参考基准采用同期同方向全部有效小时，不是波动或趋势状态匹配的因果对照。事件收益受固定退出时间影响；本研究能否定这些具体信号/持有期组合，不能证明所有 BTC 入场或其他退出方式都无效。",
        "未模拟资金费率、真实滑点、盘口成交和动态仓位；没有将事件研究冒充完整策略回测，也没有修改或替换已有八个策略。未通过入场门槛的方案不继续通过调整杠杆制造更好的曲线。",
        "",
        "[全部 90 项数值](all_results.csv) · [逐年结果（第一轮）](annual_results.csv) · [逐年结果（第二轮）](second/annual_results.csv) · [十项主检验](joint_primary_uncertainty.csv) · [最终判断](joint_decisions.json) · [数据及计算核验](verification.json)",
        "",
        "![各持有期每笔平均扣费收益，单位bp](entry_heatmap.png)",
    ]
    (ROOT / "RESULTS.md").write_text("\n".join(lines) + "\n")
    os.environ.setdefault("MPLCONFIGDIR", "/tmp/btc-entry-matplotlib")
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(1, 3, figsize=(16, 7), layout="constrained")
    order = list(LABELS)
    for ax, horizon in zip(axes, [6, 24, 48], strict=True):
        pivot = (
            table[table.horizon == horizon]
            .pivot(index="signal", columns="period", values="net_mean")
            .reindex(order)[["develop", "validation", "review"]]
            * 10000
        )
        im = ax.imshow(pivot, cmap="RdYlGn", vmin=-100, vmax=100, aspect="auto")
        ax.set_xticks(range(3), ["2021-23", "2024", "2025-26"])
        ax.set_yticks(range(len(order)), order if horizon == 6 else [""] * len(order))
        ax.set_title(f"Hold {horizon}h" + (" (primary)" if horizon == 24 else " (neighbor)"))
        for i in range(len(order)):
            for j in range(3):
                ax.text(j, i, f"{pivot.iloc[i, j]:.1f}", ha="center", va="center", fontsize=9)
    fig.colorbar(im, ax=axes, label="Mean net return per event (bp)", shrink=0.7)
    fig.savefig(ROOT / "entry_heatmap.png", dpi=140)
    plt.close(fig)
    print("ENTRY_REPORT_COMPLETE", len(table), "PRIMARY_PASSED", passed)
    print(candidate[["n", "net_mean", "extra2bp_mean", "extra5bp_mean"]].to_string())
    print(ci.to_dict())


if __name__ == "__main__":
    main()
