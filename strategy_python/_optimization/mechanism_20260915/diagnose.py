"""Describe parameter hypotheses before selecting any new trading parameters."""

import json

import numpy as np
import pandas as pd

from strategy_python._optimization.mechanism_20260915.data import ROOT


def block_interval(frame, field, seed=731):
    grouped = frame.groupby("month")[field].agg(["sum", "count"])
    if len(grouped) < 6 or grouped["count"].sum() < 30:
        return np.nan, np.nan
    rng = np.random.default_rng(seed)
    values = grouped.to_numpy()
    picks = rng.integers(0, len(values), size=(1000, len(values)))
    samples = values[picks].sum(axis=1)
    means = samples[:, 0] / samples[:, 1]
    return tuple(np.quantile(means, [0.025, 0.975]))


def main():
    data = pd.concat(
        [pd.read_parquet(ROOT / f"{asset}_paths.parquet") for asset in ("ETH", "BTC")],
        ignore_index=True,
    )
    # Round-trip taker 10bp is a diagnostic common hurdle, not a fill simulation.
    data["net_terminal"] = data.terminal_return - 0.001
    data["terminal_positive"] = (data.net_terminal > 0).astype(float)
    data["mae_atr"] = data.mae / data.atr_fraction
    data["mfe_atr"] = data.mfe / data.atr_fraction
    rows = []
    for (asset, family, variant, split, hours, eligible), group in data.groupby(
        ["asset", "family", "variant", "split", "hours", "eligible"]
    ):
        lo, hi = block_interval(group, "net_terminal")
        winners = group[group.terminal_positive == 1]
        rows.append(
            {
                "asset": asset,
                "family": family,
                "variant": variant,
                "split": split,
                "hours": hours,
                "eligible": bool(eligible),
                "n": len(group),
                "months": group.month.nunique(),
                "mean_net": group.net_terminal.mean(),
                "mean_net_ci_low": lo,
                "mean_net_ci_high": hi,
                "positive_rate": group.terminal_positive.mean(),
                "mae_median": group.mae.median(),
                "mae_q80": group.mae.quantile(0.8),
                "mae_q95": group.mae.quantile(0.95),
                "mfe_median": group.mfe.median(),
                "mfe_q75": group.mfe.quantile(0.75),
                "winner_mae_q80": winners.mae.quantile(0.8),
                "mae_atr_q80": group.mae_atr.quantile(0.8),
                "mfe_atr_median": group.mfe_atr.median(),
            }
        )
    summary = pd.DataFrame(rows)
    summary.to_csv(ROOT / "path_summary.csv", index=False)
    ranks = []
    for (asset, family, variant, split), group in data[data.eligible & (data.hours == 24)].groupby(
        ["asset", "family", "variant", "split"]
    ):
        for name, threshold, field in (
            ("stop_0.5pct", 0.005, "mae"),
            ("activation_2pct", 0.02, "mfe"),
            ("activation_3pct", 0.03, "mfe"),
            ("activation_4pct", 0.04, "mfe"),
        ):
            if family not in ("TFL", "TFS"):
                continue
            winners = group[group.terminal_positive == 1]
            ranks.append(
                {
                    "asset": asset,
                    "family": family,
                    "split": split,
                    "parameter": name,
                    "threshold": threshold,
                    "n": len(group),
                    "empirical_percentile": (group[field] <= threshold).mean(),
                    "positive_terminal_paths_inside_stop": (winners.mae <= threshold).mean()
                    if field == "mae"
                    else np.nan,
                    "winner_n": len(winners),
                }
            )
    pd.DataFrame(ranks).to_csv(ROOT / "original_parameter_percentiles.csv", index=False)
    # Original boundary locations tested as stated, not re-binned to maximize effect.
    mrl = data[
        data.eligible & (data.family == "MRL") & (data.hours == 48) & (data.variant == "2.0")
    ].copy()
    mrl["position_bin"] = pd.cut(
        mrl.risk_position,
        [-0.001, 0.17, 0.34, 0.66, 0.83, 1.001],
        labels=["0-.17", ".17-.34", ".34-.66", ".66-.83", ".83-1"],
    )
    mrl["strength_bin"] = pd.cut(
        mrl.risk_strength,
        [-0.001, 0.55, 0.8, 1.0, 1.4, np.inf],
        labels=["0-.55", ".55-.8", ".8-1", "1-1.4", ">1.4"],
    )
    risk_rows = []
    for feature in ("position_bin", "strength_bin"):
        for keys, group in mrl.groupby(["asset", "split", feature], observed=True):
            lo, hi = block_interval(group, "net_terminal")
            risk_rows.append(
                {
                    "asset": keys[0],
                    "split": keys[1],
                    "feature": feature,
                    "bin": str(keys[2]),
                    "n": len(group),
                    "months": group.month.nunique(),
                    "mae_q95": group.mae.quantile(0.95),
                    "mean_net": group.net_terminal.mean(),
                    "mean_net_ci_low": lo,
                    "mean_net_ci_high": hi,
                }
            )
    pd.DataFrame(risk_rows).to_csv(ROOT / "mrl_risk_shape.csv", index=False)
    pbs = data[data.eligible & (data.family == "PBS") & (data.hours == 24)].copy()
    pbs["old_distance_pass"] = pbs.buffered_stop_fraction.between(0.004, 0.03)
    pbs["structure_atr"] = pbs.structure_fraction / pbs.atr_fraction
    pbs.groupby(["asset", "split", "old_distance_pass"]).agg(
        n=("net_terminal", "size"),
        mean_net=("net_terminal", "mean"),
        mean_mae=("mae", "mean"),
        structure_atr_median=("structure_atr", "median"),
    ).to_csv(ROOT / "pbs_distance_gate.csv")
    lines = [
        "# 参数机制研究：首轮路径诊断",
        "",
        "本报告是事件分布诊断，不是新策略回测，也不宣称已经确定参数的真实因果来源。",
        "",
        "使用原生产指标的均线交叉、日线背景和MRL风险上下文；忽略仓位占用、恢复等待和重入状态以观察公共机会集。MRL每小时每种BOLL倍数保留首个触及机会，成交价仅为限价触及近似。",
        "",
        "标签使用入场后完整分钟，观察6/24/48小时；缺口和跨训练/检验边界路径剔除。终点收益统一扣10bp仅作诊断，不代表实际费用或止损策略收益。置信区间按月份块重采样，不能完全消除所有依赖。",
        "",
        "## 24小时、满足背景条件的公共机会",
        "",
        "| 资产 | 家族 | 版本 | 区间 | 事件数 | 月份数 | 平均费后终点收益 | 月块95%区间 |",
        "|---|---|---|---|---:|---:|---:|---:|",
    ]
    for row in summary[(summary.hours == 24) & summary.eligible].to_dict("records"):
        lines.append(
            f"| {row['asset']} | {row['family']} | {row['variant']} | {row['split']} | {row['n']} | {row['months']} | {row['mean_net']:.3%} | [{row['mean_net_ci_low']:.3%}, {row['mean_net_ci_high']:.3%}] |"
        )
    lines += [
        "",
        "## 后续判断必须使用的证据",
        "",
        "- [路径统计及6/48小时敏感性](path_summary.csv)：首先确认信号本身是否有稳定收益信息。若置信区间跨零，不能仅靠退出参数制造机制结论。",
        "- [原ETH数值在条件分布中的位置](original_parameter_percentiles.csv)：检验0.5%是否覆盖正常逆向波动，2%/3%/4%是否在两资产和不同时期代表一致事件强度。分位数对应关系不是参数最优性的证明。",
        "- [MRL原风险形状分组](mrl_risk_shape.csv)：核对双谷和递增风险是否与未来尾部损失一致；少样本区间不可用于精细拟合。",
        "- [PBS原止损范围过滤](pbs_distance_gate.csv)：检查0.4%—3%排除的机会是否确实更差；ATR已经归一化，不应再次乘资产统一波动比。",
        "",
        "尚需完成：定义并冻结统计参数方案、跨年份检验、行为测试、ETH/BTC原生回测及与旧方案比较。",
    ]
    (ROOT / "DIAGNOSTICS.md").write_text("\n".join(lines) + "\n")
    print(
        "DIAGNOSTICS_COMPLETE",
        json.dumps({"labels": len(data), "summary_rows": len(summary)}),
        flush=True,
    )


if __name__ == "__main__":
    main()
