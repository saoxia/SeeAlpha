"""Render concise conclusions from the verified, frozen experiment table."""

import json
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent


def main():
    verified = json.loads((ROOT / "formal_verification.json").read_text())
    assert verified["runs_verified"] == 112
    f = pd.read_csv(ROOT / "formal_comparison.csv")
    assert len(f) == 112

    def get(key, period="review", variant="main"):
        rows = f[(f.key == key) & (f.period == period) & (f.variant == variant)]
        assert len(rows) == 1
        return rows.iloc[0]

    lines = [
        "# BTC/ETH参数机制研究结论",
        "",
        "已完成112项正式回测、14项运行验证及28项测试。结果不支持把本轮新参数整体替换旧方案。参数可以有明确的统计定义，同时仍然缺少交易收益优势。",
        "",
        "系数仅取2021—2023；2024检验与2025-01-01至2026-08-30复核分别从500 USDT开始。后段历史此前已被查看，不能称为独立盲测。手续费已计入；未重演资金费率及真实滑点。",
        "",
        "## BTC直接比较",
        "",
        "表内收益均为USDT，回撤为新机制分钟收盘盯市最大回撤。原版和优化版均使用BTC行情。PBS统一风险预算，不能与预算不同的旧报告直接拼接。",
        "",
        "| 策略 | 区间 | 固定原版 | 固定优化版 | 比例原版 | 比例优化版 | 新机制 | 新机制回撤 |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    labels = {
        "MRL_original": "均值回归2σ",
        "MRL_optimized": "均值回归2.25σ",
        "TFL": "趋势多头",
        "TFS": "趋势空头",
        "PBS": "反弹空头",
    }
    for slot, label in labels.items():
        family = slot.split("_")[0]
        for period, title in [("validation", "2024"), ("review", "2025年以来")]:
            vals = [
                get(prefix + family + suffix, period).net_pnl
                for prefix, suffix in [
                    ("fixed_BTC_", "_original"),
                    ("fixed_BTC_", "_optimized"),
                    ("scaled_BTC_", "_original"),
                    ("scaled_BTC_", "_optimized"),
                ]
            ]
            c = get("BTC_" + slot, period)
            lines.append(
                f"| {label} | {title} | "
                + " | ".join(f"{v:.2f}" for v in vals)
                + f" | {c.net_pnl:.2f} | {c.max_drawdown_pct:.2f}% |"
            )
    lines += [
        "",
        "## 风险曲线的跨资产差异",
        "",
        "| 资产/入场 | 区间 | 保留曲线收益/回撤 | 常数风险收益/回撤 |",
        "|---|---|---:|---:|",
    ]
    for asset in ["ETH", "BTC"]:
        for slot in ["MRL_original", "MRL_optimized"]:
            for period in ["validation", "review"]:
                a, b = get(asset + "_" + slot, period), get(asset + "_" + slot, period, "flat_risk")
                lines.append(
                    f"| {asset}/{labels[slot]} | {period} | {a.net_pnl:.2f} / {a.max_drawdown_pct:.2f}% | {b.net_pnl:.2f} / {b.max_drawdown_pct:.2f}% |"
                )
    lines += [
        "",
        "BTC近期常数风险明显改善，ETH近期则明显恶化，且BTC常数风险在2024仍亏损。因此不能把复杂曲线视为跨资产通用规律，也不能直接将常数风险宣称为稳健替代。常数来自训练机会的平均乘数，只近似匹配平均预算；它改变实际成交时的风险和后续路径，并非逐笔等风险的纯形状比较。",
        "",
    ]
    lines += [
        "",
        "MRL保留两个布林带入场条件；趋势原/优化版不同的启动百分比由同一统计定义替代，因此每个方向只有一个新机制候选。",
        "",
        "## 哪些解释得到支持",
        "",
        "- 信号本身也需要检验：BTC近期合格机会的24小时终点净收益月块区间均跨零，6/48小时结果亦不构成普遍稳定优势。这是公共机会诊断，并非实际策略收益；它提醒我们，调整退出尺度无法自动证明入场存在稳定优势。",
        "- 趋势止损：BTC多头的恢复路径覆盖率在2024/近期约82.8%/82.3%，接近设计的80%。这支持ATR条件分位的可迁移统计含义，但实际盈利没有因此成立。",
        "- 小止损的另一种作用是约束单次亏损，依靠少量大趋势获得较高盈亏比。新方案胜率上升但平均亏损扩大；不能把恢复覆盖率直接当作优化指标，也不能据此认定旧0.5%是唯一最优值。",
        "- MRL回归前逆向距离除以布林带距离后，两资产训练分位相近；但将它用于加仓/止盈并未解决BTC尾部亏损。“尺度相似”与“有正期望”是两回事。回中轨距离在多数机会中小于原0.5%，也需要检查费用与大亏损的关系。",
        "- PBS的80%缓冲分位两资产都为零，而90%分位约为1ATR，说明分布集中在结构高点以内、尾部变化较大。这不能唯一识别原0.25ATR的来源，也不能证明零缓冲最优。",
        "- MRL位置/强度曲线的精确结点未获得独特统计依据；常数风险实验只是在新机制内的条件对照。恢复时间带缺少独立事件和完整释放状态，仍属未知。65%锁利、4R、25%灾难止损及保留周期也未被本轮单独验证。",
        "",
        "## 候选邻域与成本",
        "",
        "| 资产/策略 | 区间 | 主方案收益 | 预声明邻域收益范围 | 主方案加每侧5bp成本 |",
        "|---|---|---:|---:|---:|",
    ]
    for asset in ["ETH", "BTC"]:
        for slot in ["TFL", "TFS", "PBS"]:
            for period in ["validation", "review"]:
                c = get(asset + "_" + slot, period)
                n = f[(f.key == c.key) & (f.period == period) & (f.variant != "main")]
                lines.append(
                    f"| {asset}/{labels[slot]} | {period} | {c.net_pnl:.2f} | {n.net_pnl.min():.2f} — {n.net_pnl.max():.2f} | {c.extra_5bp_pnl:.2f} |"
                )
    lines += [
        "",
        "邻域仅作敏感性检查，没有按后期收益回选主参数。成本压力沿用固定成交路径，未按扣费后的权益重新定仓。跨时期胜负变化、成本敏感及少数大盈利集中，都限制了“已排除过拟合”的结论。",
        "",
        "趋势同时调整了仓位上限内的计划风险，MRL还修复订单快照/迟到成交处理，因此新旧差异不全是参数的单因素效果。未覆盖旧策略，候选作为独立研究包保留。",
        "",
        "## 证据和复现",
        "",
        "- [完整结果、ETH比较与常数风险消融](FORMAL_RESULTS.md)",
        "- [全部112项数值](formal_comparison.csv) · [年度收益](formal_comparison.json)",
        "- [跨时期统计定义复核](MECHANISM_EVIDENCE.md) · [成交盈亏结构](TRADE_PAYOFF_EVIDENCE.md)",
        "- [冻结参数定义](CANDIDATE_DEFINITION.md) · [参数系数及训练样本](candidate_coefficients.json)",
        "- [运行核验](formal_verification.json) · [候选使用入口](../../_mechanism/README.md)",
    ]
    (ROOT / "CONCLUSIONS.md").write_text("\n".join(lines) + "\n")
    print("CONCLUSIONS_COMPLETE")


if __name__ == "__main__":
    main()
