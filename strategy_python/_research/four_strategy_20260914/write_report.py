"""Report frozen native results; no parameter selection occurs here."""

import hashlib
import json
import os
from pathlib import Path

import pandas as pd

from strategy_python._research.four_strategy_20260914.materialize import PACKAGES
from strategy_python._research.four_strategy_20260914.research import BASE, CANDIDATES, NAMES

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parents[2]
LABELS = {
    "MRL": "均值回归做多",
    "TFL": "趋势跟随做多",
    "TFS": "趋势跟随做空",
    "PBS": "小时反弹做空",
}


def main():
    source_hashes = json.loads((ROOT / "original_hashes.json").read_text())
    changed = [
        p
        for p, h in source_hashes.items()
        if hashlib.sha256((PROJECT / p).read_bytes()).hexdigest() != h
    ]
    if changed:
        raise RuntimeError(f"Original files changed: {changed}")
    frozen_hashes = json.loads((ROOT / "frozen_source_hashes.json").read_text())["sha256"]
    if any(
        hashlib.sha256((PROJECT / p).read_bytes()).hexdigest() != h
        for p, h in frozen_hashes.items()
    ):
        raise RuntimeError("Delivered strategy source changed after freezing")
    rows = []
    selected = {
        k: json.loads((ROOT / f"{k}_selection.json").read_text())["selected"] for k in NAMES
    }
    for key in NAMES:
        for period in ("train", "validation", "holdout", "full"):
            base = json.loads((ROOT / "runs" / key / period / "baseline/metrics.json").read_text())
            newroot = "runs" if period in ("train", "validation") else "frozen_runs"
            opt = json.loads(
                (ROOT / newroot / key / period / selected[key] / "metrics.json").read_text()
            )
            rows.append(
                {
                    "strategy": key,
                    "period": period,
                    "candidate": selected[key],
                    "baseline_pnl": base["net_pnl"],
                    "optimized_pnl": opt["net_pnl"],
                    "baseline_return_pct": base["return_pct"],
                    "optimized_return_pct": opt["return_pct"],
                    "baseline_dd_pct": base["max_drawdown_pct"],
                    "optimized_dd_pct": opt["max_drawdown_pct"],
                    "baseline_trades": base["trades"],
                    "optimized_trades": opt["trades"],
                    "baseline_stress_5bp_pnl": base["stress"]["5"]["net_pnl"],
                    "optimized_stress_5bp_pnl": opt["stress"]["5"]["net_pnl"],
                }
            )
    frame = pd.DataFrame(rows)
    frame.to_csv(ROOT / "comparison.csv", index=False)
    (ROOT / "comparison.json").write_text(json.dumps(rows, indent=2) + "\n")
    os.environ.setdefault("MPLCONFIGDIR", str(PROJECT / ".local/cache/matplotlib"))
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(2, 2, figsize=(14, 8), layout="constrained")
    for ax, key in zip(axes.flat, NAMES):
        for root, name, label, color in [
            ("runs", "baseline", "Original", "#c17835"),
            ("frozen_runs", selected[key], "Selected", "#2366b1"),
        ]:
            daily = pd.read_csv(
                ROOT / root / key / "full" / name / "daily_equity.csv",
                index_col=0,
                parse_dates=True,
            )
            ax.plot(daily.index, (daily.equity / 500 - 1) * 100, label=label, color=color, lw=1.1)
        ax.set_title(key + " | " + selected[key])
        ax.set_ylabel("Return (%)")
        ax.axhline(0, color="#999", lw=0.5)
        ax.grid(alpha=0.15)
        ax.legend(frameon=False)
    fig.suptitle("Independent strategies | 500 USDT each | 1-minute native backtest", fontsize=15)
    fig.savefig(ROOT / "equity_comparison.png", dpi=160)
    plt.close(fig)
    text = [
        "# 四策略独立优化结果",
        "",
        "[盈亏比、最大回撤和总成交金额完整对照](TRADE_METRICS.md) · [逐项数据 CSV](trade_metrics.csv)",
        "",
        "**结论：只有趋势空头新版在开发、验证和本轮留出期都提高净收益。均值回归多头、趋势多头虽全历史收益提高，但留出期退步，暂不建议替换原版；小时反弹空头保留原规则。**",
        "",
        "标的 ETHUSDT 永续；每个策略独立以 500 USDT 起步，账户保证金杠杆 5 倍。所有比较使用 1 分钟输入和原生 Nautilus 引擎；已扣模拟手续费 maker 0.02%、taker 0.05%，未含历史资金费率和真实滑点。原策略文件 SHA256 复核全部一致。",
        "",
        "**“最优”仅指本次已测试、满足开发/验证门槛的候选。不能保证全局最优或未来收益。**",
        "每个策略开发期初筛 12 个有明确假设的配置，共 48 个；各取原版、3 个首轮入围项和 1 个组合项，合计 20 个配置进行原生分钟级开发/验证。最终从通过门槛的入围项中选取验证期净收益最高者，并直接使用交付新类的默认值完成最终评估。",
        "",
        "## 全历史固定版本复核（2021-01-01 至 2026-09-01）",
        "",
        "| 策略 | 原版净收益 USDT | 新版净收益 USDT | 原版收益率 | 新版收益率 | 原版回撤 | 新版回撤 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for r in rows:
        if r["period"] == "full":
            text.append(
                f"| {LABELS[r['strategy']]} | {r['baseline_pnl']:.2f} | {r['optimized_pnl']:.2f} | {r['baseline_return_pct']:.2f}% | {r['optimized_return_pct']:.2f}% | {r['baseline_dd_pct']:.2f}% | {r['optimized_dd_pct']:.2f}% |"
            )
    text += [
        "",
        "![独立策略权益曲线](equity_comparison.png)",
        "",
        "## 本轮留出评估（2025-01-01 至 2026-09-01）",
        "",
        "参数在看该区间结果前固定。该区间曾被原策略开发使用，所以不是完全未见的新样本。收益是各区间重新以 500 USDT 起步的结果，不能直接相加。",
        "",
        "| 策略 | 原版净收益 | 新版净收益 | 原版回撤 | 新版回撤 |",
        "|---|---:|---:|---:|---:|",
    ]
    for r in rows:
        if r["period"] == "holdout":
            text.append(
                f"| {LABELS[r['strategy']]} | {r['baseline_pnl']:.2f} | {r['optimized_pnl']:.2f} | {r['baseline_dd_pct']:.2f}% | {r['optimized_dd_pct']:.2f}% |"
            )
    worse = [
        LABELS[r["strategy"]]
        for r in rows
        if r["period"] == "holdout" and r["optimized_pnl"] < r["baseline_pnl"] - 0.01
    ]
    if worse:
        text += [
            "",
            "**留出期收益退步的成员："
            + "、".join(worse)
            + "。参数没有据此回调，不能声称新版全面优于原版。**",
            "",
        ]
    text += [
        "",
        "## 开发与验证证据",
        "",
        "| 策略 | 开发原版/新版净收益 | 验证原版/新版净收益 |",
        "|---|---:|---:|",
    ]
    for key in NAMES:
        train = next(r for r in rows if r["strategy"] == key and r["period"] == "train")
        val = next(r for r in rows if r["strategy"] == key and r["period"] == "validation")
        text.append(
            f"| {LABELS[key]} | {train['baseline_pnl']:.2f} / {train['optimized_pnl']:.2f} | "
            f"{val['baseline_pnl']:.2f} / {val['optimized_pnl']:.2f} |"
        )
    text += ["", "## 选择与解释", ""]
    for key in NAMES:
        params = {**BASE[key], **CANDIDATES[key][selected[key]]}
        text += [
            f"### {LABELS[key]}：{selected[key]}",
            "",
            f"新目录：`strategy_python/{PACKAGES[key]}`。相对原策略默认配置：`{json.dumps(params, ensure_ascii=False)}`。",
            "",
        ]
        if selected[key] == "baseline":
            text += [
                "本轮没有候选通过全部改进门槛，新文件保留原组合交易规则，不能称作收益已提升。",
                "",
            ]
        explanations = {
            "MRL": "要求 BOLL(20) 下轨有更深折价：标准差倍数 2.0 → 2.25。原四层风险定仓、0.5% 止盈与恢复门槛保留。max_holding_hours=48 仅限制加仓窗口，不是强制持仓期限。",
            "TFL": "盈利达到 3% 后才启动锁定峰值盈利 65% 的移动保护，给上涨趋势更多延续空间；初始止损仍为 0.5%，名义仓位仍为权益 1 倍。",
            "TFS": "盈利达到 4% 后才启动锁定峰值盈利 65% 的移动保护，保留较长下跌段；初始止损、日线过滤与权益 1 倍定仓保留。",
            "PBS": "12 小时结构止损、4R 止盈、24 小时时间退出维持原样。0.015 风险比例和 3 倍名义上限来自原组合覆盖参数，基准同样使用这些数值。",
        }
        text += [explanations[key], "", f"[独立策略说明](../../{PACKAGES[key]}/README.md)", ""]
    text += [
        "## 额外成本压力（全历史）",
        "",
        "每次成交额外扣 5bp，固定原成交路径和仓位；并非滑点重跑。",
        "",
        "| 策略 | 原版剩余净收益 | 新版剩余净收益 |",
        "|---|---:|---:|",
    ]
    for r in rows:
        if r["period"] == "full":
            text.append(
                f"| {LABELS[r['strategy']]} | {r['baseline_stress_5bp_pnl']:.2f} | "
                f"{r['optimized_stress_5bp_pnl']:.2f} |"
            )
    text += [
        "",
        "## 成本、数据与适用范围",
        "",
        "- 回撤包含未实现盈亏，按每分钟收盘价估值；不代表分钟内部最坏权益。",
        "- 资金费率、真实盘口深度与市场冲击未重放。每笔额外 2/5/10bp 成本压力见各 metrics.json；这是固定成交路径敏感性，不是带滑点重新回测。",
        "- 原始分钟数据在开发期缺 584 分钟、验证期缺 15 分钟、留出期缺 19 分钟，全历史合计 618 分钟；每个运行记录 missing_minutes、首末行情时间。5 分钟筛选只保留完整桶，因此丢弃分钟数会高于原始缺口。",
        "- PBS 使用组合原有 risk_fraction=0.015、max_notional_fraction=3；其他策略保持原定仓。提高倍数没有作为优化手段。",
        "- 新组合清单供组装使用。这里展示独立账户结果，不能相加后冒充共享账户组合收益。",
        "- 原版和新版本使用相同原生 OHLC 成交假设；有关执行顺序可参阅 [Nautilus 官方说明](https://nautilustrader.io/docs/latest/concepts/backtesting/execution-flow/)，本次实际版本以 parameters.json 为准。",
        "",
        "## 复现与明细",
        "",
        "993 项策略测试通过；49 个原策略文件校验值未变；四个策略在活跃交易区间的分批加载与一次性加载成交、权益指标完全一致。新组合入口已完成 2024 年 3 月集成回测，详细路径见 portfolio_smoke.json。",
        "实验协议见 [PROTOCOL.md](PROTOCOL.md)；逐项比较见 [comparison.csv](comparison.csv)。筛选记录在 screen_5m/，原生开发/验证和原版复核在 runs/，交付新类的最终回测在 frozen_runs/。每次运行保存配置、成交、持仓、账户、每日权益与指标。",
        "",
        "研究入口：`.venv/bin/python -m strategy_python._research.four_strategy_20260914.research <MRL|TFL|TFS|PBS> <train|validation|holdout|full> --candidates <候选名称>`。复核交付类加 `--optimized`。",
    ]
    (ROOT / "RESULTS.md").write_text("\n".join(text) + "\n")
    print(frame.to_string(index=False))


if __name__ == "__main__":
    main()
