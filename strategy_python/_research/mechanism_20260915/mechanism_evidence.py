"""Out-of-training distribution checks for the declared parameter meanings."""

import json

import pandas as pd

from strategy_python._research.mechanism_20260915.data import ROOT
from strategy_python._research.mechanism_20260915.diagnose import block_interval


def main():
    coefficients = json.loads((ROOT / "candidate_coefficients.json").read_text())["coefficients"]
    annual = json.loads((ROOT / "annual_coefficients.json").read_text())
    data = pd.concat(
        [pd.read_parquet(ROOT / f"{asset}_mechanism_paths.parquet") for asset in ("ETH", "BTC")],
        ignore_index=True,
    )
    rows = []
    for (asset, family, variant, split), group in data.groupby(
        ["asset", "family", "variant", "split"]
    ):
        recovered = group[group.recovered].copy()
        key = "MRL_" + variant if family == "MRL" else family
        coefficient = coefficients[asset][key]
        checks = []
        if family in ("TFL", "TFS"):
            checks = [
                (
                    "noise_stop_coverage",
                    recovered,
                    recovered.noise_ratio <= coefficient["stop_atr"],
                    0.8,
                ),
                (
                    "old_0.5pct_stop_coverage",
                    recovered,
                    recovered.noise_before_recovery <= 0.005,
                    None,
                ),
                ("activation_cdf", group, group.mfe_ratio <= coefficient["activation_atr"], 0.5),
            ]
        elif family == "MRL":
            checks = [
                (f"add_noise_cdf_q{int(q * 100)}", recovered, recovered.noise_ratio <= value, q)
                for q, value in zip((0.25, 0.5, 0.75), coefficient["add_band_multipliers"])
            ]
            checks += [
                (
                    f"old_add_{distance:.3f}_cdf",
                    recovered,
                    recovered.noise_before_recovery <= distance,
                    None,
                )
                for distance in (0.005, 0.01, 0.02)
            ]
        elif family == "PBS":
            checks = [
                (
                    "buffer_coverage",
                    recovered,
                    recovered.overshoot_atr <= coefficient["stop_atr_buffer"],
                    0.8,
                ),
                ("old_0.25ATR_buffer_coverage", recovered, recovered.overshoot_atr <= 0.25, None),
            ]
        for name, sample, outcomes, target in checks:
            sample = sample.assign(covered=outcomes.astype(float))
            lo, hi = block_interval(sample, "covered")
            rows.append(
                {
                    "asset": asset,
                    "family": family,
                    "variant": variant,
                    "split": split,
                    "check": name,
                    "n": len(sample),
                    "months": sample.month.nunique(),
                    "design_target": target,
                    "actual_coverage": sample.covered.mean(),
                    "month_block_ci_low": lo,
                    "month_block_ci_high": hi,
                }
            )
    pd.DataFrame(rows).to_csv(ROOT / "mechanism_distribution_checks.csv", index=False)
    lines = [
        "# 参数含义的跨时期复核",
        "",
        "这些检查检验统计含义能否迁移，不等于检验策略盈利。训练分位数的训练覆盖率接近目标属于定义结果，主要看validation/review。覆盖率使用恢复子样本时，必须同时关注未恢复样本的损失；不能把条件覆盖率当胜率。",
        "",
        "## 趋势多头：年度估计",
        "",
        "| 资产 | 年份 | 止损ATR倍数 | 启动ATR倍数 |",
        "|---|---|---:|---:|",
    ]
    for asset in ("ETH", "BTC"):
        for year in ("2021", "2022", "2023"):
            c = annual[year][asset]["TFL"]
            lines.append(f"| {asset} | {year} | {c['stop_atr']:.4f} | {c['activation_atr']:.4f} |")
    lines += [
        "",
        "完整年度系数含MRL、TFS、PBS见[年度参数](annual_coefficients.json)。不同年份因样本数量可能触发回退，不应把来源不同的估计当同一个窄置信区间。",
        "",
        "## 训练后统计含义",
        "",
        "| 资产/家族 | 区间 | 检查 | 样本数 | 覆盖率 | 月块95%区间 |",
        "|---|---|---|---:|---:|---:|",
    ]
    for row in rows:
        if row["split"] == "train" or row["family"] == "MRL":
            continue
        lines.append(
            f"| {row['asset']}/{row['family']} | {row['split']} | {row['check']} | {row['n']} | {row['actual_coverage']:.1%} | [{row['month_block_ci_low']:.1%}, {row['month_block_ci_high']:.1%}] |"
        )
    lines += [
        "",
        "NaN区间表示样本少于30或月份少于6，未强行计算。完整MRL加仓分位覆盖率见[数据](mechanism_distribution_checks.csv)。",
        "",
        "## 原未知参数可以解释到什么程度",
        "",
        "| 参数 | 可检验含义 | 本轮不能声称的结论 |",
        "|---|---|---|",
        "| 趋势0.5%止损 | 恢复前逆向波动的覆盖范围；新方案按入场ATR和条件分位定义 | 不能说0.5%曾经就是按该分位选的，也不能说放宽必定更赚钱 |",
        "| 趋势2%/3%/4%启动 | 在信号后MFE分布中的位置；新方案用典型顺向幅度 | 中位数不是已识别趋势拐点，ETH的优化值不自动适用于BTC |",
        "| MRL 0.5%/1%/2%加仓 | 回归前逆向距离，相对于当时下轨至中轨距离 | 原1:2:4间距未被证明最优；分位接近不证明加仓有正期望 |",
        "| MRL 0.5%止盈 | 与局部均值距离比较，检验固定百分比是否忽略市场状态 | 平均成本变化后的新目标不一定仍等于原中轨，手续费不可忽略 |",
        "| MRL双谷/强度风险曲线 | 条件尾部损失与常数预算消融 | 几个分箱及消融不能证明17%/83%等精确结点有独特依据 |",
        "| PBS 0.25ATR缓冲 | 恢复前超出结构高点的噪声覆盖率 | 80%分位为零不能证明零缓冲最优；90%尾部可能完全不同 |",
        "| PBS 0.4%—3%止损范围 | 结构相对ATR的异常程度 | 中心80%仅是设计约束，不是排除两端必然增益的证明 |",
        "| MRL恢复时间带 | 需要足够多硬止损和完整释放状态事件 | 当前事件不足且释放时点未完整记录，不能恢复精确公式来源 |",
        "| 周期、65%锁利、4R、25%硬止损及资金预算 | 本轮保留的信号/风险设计 | 没有单独证据，不包装成新发现的市场规律 |",
        "",
        "最终是否采用候选仍以正式回测和邻域表现判断；即使表现更好，也只能获得可支持的统计解释，不能从历史价格唯一识别原作者当时选数的原因。",
    ]
    (ROOT / "MECHANISM_EVIDENCE.md").write_text("\n".join(lines) + "\n")
    print("MECHANISM_DISTRIBUTIONS_CHECKED", len(rows))


if __name__ == "__main__":
    main()
