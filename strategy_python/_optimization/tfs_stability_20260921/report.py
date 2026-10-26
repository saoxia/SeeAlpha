"""Report every frozen cell, without smoothing or choosing a new optimum."""

import json
from collections import Counter, deque
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.colors import TwoSlopeNorm
from matplotlib.patches import Rectangle

from .study import LOCKS, PROJECT, ROOT, STOP_SCALES, load_settings, source_files, verify


def verify_inventory(manifest):
    """Also reject added input files, which an existing-file hash check cannot detect."""
    sources = {str(p.relative_to(PROJECT)) for p in source_files()}
    catalog = Path(load_settings().catalog_dir)
    data = {
        str(p.relative_to(PROJECT))
        for p in catalog.rglob("*.parquet")
        if any(
            token in str(p)
            for asset in ("BTC", "ETH")
            for token in (
                f"{asset}USDT-PERP.BINANCE-1-MINUTE-LAST-EXTERNAL",
                f"crypto_perpetual/{asset}USDT",
            )
        )
        or (
            "crypto_perpetual" in str(p)
            and any(f"{asset}USDT" in str(p) for asset in ("BTC", "ETH"))
        )
    }
    for label, current in (("source", sources), ("data", data)):
        expected = set(manifest[label + "_sha256"])
        if current != expected:
            raise RuntimeError(
                f"Frozen {label} inventory changed: added={sorted(current - expected)}, "
                f"removed={sorted(expected - current)}"
            )
    return {"source_files": len(sources), "data_files": len(data), "unchanged": True}


def monthly_interval(daily, initial, *, repeats=5000, block=3):
    """Circular moving blocks of monthly equity returns; descriptive, post-selection CI."""
    monthly = daily.equity.resample("ME").last()
    returns = monthly.pct_change()
    returns.iloc[0] = monthly.iloc[0] / initial - 1
    values = returns.to_numpy()
    if len(values) < 2 * block or not np.isfinite(values).all():
        return None, None, None
    rng = np.random.default_rng(20260921)
    starts = rng.integers(0, len(values), size=(repeats, int(np.ceil(len(values) / block))))
    indices = (starts[:, :, None] + np.arange(block)) % len(values)
    sampled = values[indices.reshape(repeats, -1)[:, : len(values)]].mean(axis=1)
    lower, upper = np.quantile(sampled, [0.025, 0.975]) * 100
    return float(values.mean() * 100), float(lower), float(upper)


def component(mask, origin=(2, 2)):
    if not mask[origin]:
        return set()
    seen = {origin}
    todo = deque([origin])
    while todo:
        row, col = todo.popleft()
        for neighbor in ((row - 1, col), (row + 1, col), (row, col - 1), (row, col + 1)):
            a, b = neighbor
            if (
                0 <= a < mask.shape[0]
                and 0 <= b < mask.shape[1]
                and mask[a, b]
                and neighbor not in seen
            ):
                seen.add(neighbor)
                todo.append(neighbor)
    return seen


def grid_values(frame, asset, fee, value):
    selected = frame[(frame.asset == asset) & (frame.extra_fee_bp == fee) & (frame.kind == "grid")]
    return (
        selected.pivot(index="stop_scale", columns="lock", values=value)
        .reindex(index=STOP_SCALES, columns=LOCKS)
        .to_numpy(dtype=float)
    )


def gate(frame):
    by_asset = {}
    for asset in ("BTC", "ETH"):
        mask = np.ones((5, 5), dtype=bool)
        for fee in (0, 5):
            mask &= grid_values(frame, asset, fee, "net_pnl") > 0
            mask &= grid_values(frame, asset, fee, "review_marked_pnl") > 0
            mask &= grid_values(frame, asset, fee, "entry_fills_below_snapshot_min_notional") == 0
        mask &= grid_values(frame, asset, 0, "trades") >= 50
        mask &= grid_values(frame, asset, 0, "review_closed_trades") >= 20
        by_asset[asset] = mask
    common = by_asset["BTC"] & by_asset["ETH"]
    connected = component(common)
    passed = (
        len(connected) >= 4
        and len({x for x, y in connected}) >= 2
        and len({y for x, y in connected}) >= 2
    )
    return {
        "passed": passed,
        "eligible_counts": {a: int(m.sum()) for a, m in by_asset.items()},
        "common_eligible_count": int(common.sum()),
        "center_component_count": len(connected),
        "center_component": [[STOP_SCALES[x], LOCKS[y]] for x, y in sorted(connected)],
        "common_mask": common.tolist(),
        "asset_masks": {a: m.tolist() for a, m in by_asset.items()},
    }


def draw_grid(ax, values, title, norm, *, mark_center=True):
    im = ax.imshow(values, cmap="RdYlGn", norm=norm, interpolation="nearest", aspect="auto")
    ax.set_xticks(range(5), [f"{x:.0%}" for x in LOCKS])
    ax.set_yticks(range(5), [f"{x:.1f}x" for x in STOP_SCALES])
    ax.set_xlabel("Profit lock")
    ax.set_ylabel("Stop ATR multiplier")
    ax.set_title(title)
    for (row, col), value in np.ndenumerate(values):
        ax.text(col, row, f"{value:.2f}", ha="center", va="center", fontsize=9, color="black")
    if mark_center:
        ax.add_patch(Rectangle((1.5, 1.5), 1, 1, fill=False, edgecolor="#143a72", linewidth=3))
    return im


def heatmaps(frame):
    for field, filename, title in (
        ("return_pct", "heatmap_full.png", "Full history net return (%)"),
        (
            "review_marked_pnl",
            "heatmap_recent.png",
            "2025 onward marked PnL (USDT), continuous account",
        ),
    ):
        values = [grid_values(frame, a, f, field) for a in ("BTC", "ETH") for f in (0, 5)]
        limit = max(0.01, max(float(np.max(np.abs(x))) for x in values))
        norm = TwoSlopeNorm(vmin=-limit, vcenter=0, vmax=limit)
        fig, axes = plt.subplots(2, 2, figsize=(11, 9), layout="constrained")
        for ax, (asset, fee), array in zip(
            axes.flat, [(a, f) for a in ("BTC", "ETH") for f in (0, 5)], values, strict=True
        ):
            im = draw_grid(
                ax,
                array,
                f"{asset} | " + ("base fees" if fee == 0 else "+5 bp per fill, rerun"),
                norm,
            )
        fig.suptitle(title + "\nBlue box: frozen center. No interpolation.", fontsize=14)
        fig.colorbar(im, ax=axes.ravel().tolist(), shrink=0.8)
        fig.savefig(ROOT / filename, dpi=160)
        plt.close(fig)
    for asset in ("BTC", "ETH"):
        fields = [f"annual_{year}" for year in range(2021, 2027)]
        values = [grid_values(frame, asset, 0, f) for f in fields]
        limit = max(0.01, max(float(np.max(np.abs(x))) for x in values))
        fig, axes = plt.subplots(2, 3, figsize=(15, 9), layout="constrained")
        for ax, year, array in zip(axes.flat, range(2021, 2027), values, strict=True):
            im = draw_grid(
                ax,
                array,
                str(year) + (" through Aug 30" if year == 2026 else ""),
                TwoSlopeNorm(vmin=-limit, vcenter=0, vmax=limit),
            )
        fig.suptitle(
            f"{asset} annual marked PnL (USDT), base fees, continuous account", fontsize=15
        )
        fig.colorbar(im, ax=axes.ravel().tolist(), shrink=0.8)
        fig.savefig(ROOT / f"annual_{asset}.png", dpi=140)
        plt.close(fig)


def table(frame, columns, labels=None):
    labels = labels or columns
    lines = ["| " + " | ".join(labels) + " |", "|" + "---|" * len(columns)]
    for _, row in frame.iterrows():
        values = []
        for column in columns:
            value = row[column]
            values.append(
                "—"
                if pd.isna(value)
                else f"{value:.2f}"
                if isinstance(value, (float, np.floating))
                else str(value)
            )
        lines.append("| " + " | ".join(values) + " |")
    return "\n".join(lines)


def main():
    manifest = json.loads((ROOT / "manifest.json").read_text())
    verify(manifest, data=True)
    inventory = verify_inventory(manifest)
    rows = []
    audits = []
    for row in manifest["experiments"]:
        directory = ROOT / "runs" / row["id"]
        stats = json.loads((directory / "metrics.json").read_text())
        parameters = json.loads((directory / "parameters.json").read_text())
        if parameters["experiment"] != row:
            raise RuntimeError("Unexpected experiment metadata")
        daily = pd.read_csv(directory / "daily_equity.csv", index_col=0, parse_dates=True)
        mean, lower, upper = monthly_interval(daily, row["starting_balance"])
        record = {
            **row,
            **{k: v for k, v in stats.items() if not isinstance(v, dict)},
            "monthly_mean_pct": mean,
            "monthly_ci_low_pct": lower,
            "monthly_ci_high_pct": upper,
            **{f"annual_{y}": pnl for y, pnl in stats["annual_marked_pnl"].items()},
        }
        record["positive_years"] = sum(v > 0 for v in stats["annual_marked_pnl"].values())
        fills = pd.read_csv(directory / "fills.csv")
        min_notional = float(parameters["instrument"]["min_notional"].split()[0])
        entries = fills[~fills.is_reduce_only.astype(bool)] if len(fills) else fills
        below = (
            int(
                (
                    (entries.filled_qty.astype(float) * entries.avg_px.astype(float))
                    < min_notional - 1e-8
                ).sum()
            )
            if len(entries)
            else 0
        )
        record["entry_fills_below_snapshot_min_notional"] = below
        rows.append(record)
        audits.append(
            {
                "id": row["id"],
                "cashflow_error": stats["cashflow_error"],
                "off_tick": stats["off_tick_order_price_fields"],
                "missing_minutes": stats["missing_minutes"],
                "entry_fills_below_snapshot_min_notional": below,
                "order_event_reasons": stats["order_event_reasons"],
            }
        )
    frame = pd.DataFrame(rows)
    event_types = Counter()
    for audit in audits:
        for reason, count in audit["order_event_reasons"].items():
            event_types[reason.split(":", 1)[0]] += count
    frame.to_csv(ROOT / "comparison.csv", index=False)
    decision = gate(frame)
    (ROOT / "decision.json").write_text(json.dumps(decision, indent=2) + "\n")
    (ROOT / "execution_audit.json").write_text(json.dumps(audits, indent=2) + "\n")
    (ROOT / "input_inventory_audit.json").write_text(json.dumps(inventory, indent=2) + "\n")
    heatmaps(frame)
    summary = []
    for asset in ("BTC", "ETH"):
        for fee in (0, 5):
            cells = frame[
                (frame.asset == asset) & (frame.extra_fee_bp == fee) & (frame.kind == "grid")
            ]
            summary.append(
                {
                    "asset": asset,
                    "fee": fee,
                    "positive": int((cells.net_pnl > 0).sum()),
                    "recent_positive": int((cells.review_marked_pnl > 0).sum()),
                    "median": float(cells.return_pct.median()),
                    "q25": float(cells.return_pct.quantile(0.25)),
                    "worst_dd": float(cells.max_drawdown_pct.max()),
                }
            )
    center = frame[(frame.kind == "grid") & (frame.stop_scale == 1) & (frame.lock == 0.8)]
    diagnostics = frame[frame.kind != "grid"]
    if decision["passed"]:
        conclusion = "冻结中心通过预声明的共同区域筛查，可进入另行冻结的未来时间验证；本轮不据此换参或宣称排除过拟合。"
    else:
        conclusion = "冻结中心未通过预声明的跨市场稳定区域筛查，停止扩大该候选的参数搜索；不从图中另选最高点替换现行参数。"
    text = f"""# 趋势空头稳定域检验结果

{conclusion}

完成全部106次原生回测：ETH/BTC各25格×两档成本、中心额外2bp及5000账户、固定原版对照。
统一UTC 2021-01-01至2026-08-30，历史已经参与过开发与选择，全部属于事后诊断。
一般本金500 USDT，5倍账户杠杆；表内收益率均为整个区间累计，不是年化。
修正前BTC中心已逐笔复现旧报告；随后发现保证金账户跳过最低名义金额检查。
本报告全部重新执行开仓数量/金额约束，参数网格不变。修正前后对照见[核对](center_reconciliation.json)。
修正前45次结果保留在before_constraints，不混入本表。

## 参数邻域

{table(pd.DataFrame(summary), ["asset", "fee", "positive", "recent_positive", "median", "q25", "worst_dd"], ["市场", "每次成交额外bp", "全期盈利格/25", "2025后盈利格/25", "收益率中位%", "收益率下四分位%", "格点最大回撤%"])}

BTC满足成本/时间/笔数条件的格点{decision["eligible_counts"]["BTC"]}个，ETH为{decision["eligible_counts"]["ETH"]}个；
两市场共同格点{decision["common_eligible_count"]}个，冻结中心所在共同连通块{decision["center_component_count"]}格。
门槛在运行前写入[协议](PROTOCOL.md)，不是看图后决定。

图中横轴为锁利比例，纵轴为止损ATR系数相对原值的倍数（0.8倍即缩小20%）。
蓝框是运行前冻结的中心；绿色为盈利，红色为亏损。全期图使用累计收益率，近期图使用连续账户的USDT盈亏。

![完整历史收益率](heatmap_full.png)

![连续账户近期收益](heatmap_recent.png)

## 冻结中心及对照

{table(center, ["asset", "extra_fee_bp", "return_pct", "max_drawdown_pct", "profit_factor", "trades", "review_marked_pnl", "positive_years", "pnl_without_best5"], ["市场", "额外bp", "累计收益%", "回撤%", "净利润因子", "交易数", "2025后盈亏", "盈利年度数", "剔除最佳5笔后盈亏"])}

{table(diagnostics, ["id", "return_pct", "max_drawdown_pct", "trades", "review_marked_pnl"], ["诊断", "累计收益%", "回撤%", "交易数", "2025后盈亏"])}

原版对照是固定4%价格启动、65%锁利的ETH优化版，同样逻辑在两资产运行。
候选两市场均使用BTC冻结的ATR系数，ETH不另行拟合；因此失败不等同于否定所有ETH趋势空头。
5000账户只检验金额/数量取整和复利路径敏感性，不能把收益差直接归因于信号改善。
“剔除最佳5笔”是保留交易路径的集中度诊断，不是删除交易后重新回测。

## 时间分布与不确定性

![BTC年度](annual_BTC.png)

![ETH年度](annual_ETH.png)

{table(center[["asset", "extra_fee_bp", "monthly_mean_pct", "monthly_ci_low_pct", "monthly_ci_high_pct"]], ["asset", "extra_fee_bp", "monthly_mean_pct", "monthly_ci_low_pct", "monthly_ci_high_pct"], ["市场", "额外bp", "平均月收益%", "时间块区间下限%", "时间块区间上限%"])}

区间为3个月循环移动时间块、5000次重采样的95%百分位区间；2026年8月只到30日。
它描述既有收益序列的不确定性，没有修正历次策略选择，也不是未来盈利概率或独立显著性证明。
2025后盈亏与各年度来自同一次连续账户，包含当时浮动权益；不等同于每段重新投入500。
回撤按分钟收盘盯市，年度图用权益变化，不能代替分钟内最坏路径。

## 执行核验及限制

全部运行核对成交现金流、末尾平仓和账户；价格步长异常为{sum(a["off_tick"] for a in audits)}。
按目录合约快照的最低名义金额检查，低于该金额的入场成交共{sum(a["entry_fills_below_snapshot_min_notional"] for a in audits)}笔（不同实验重复计数）。
各运行的缺失分钟、取消/拒绝原因和最低名义核对保存在[执行审计](execution_audit.json)。
订单事件包含撤单被拒{event_types["OrderCancelRejected"]}次、修改被拒{event_types["OrderModifyRejected"]}次（按实验重复计数）。
候选策略对止损修改被拒保留原有处理路径：撤销入场并发起全平；实际成交与费用已包含在本次回放结果中。
成本压力通过修改实际模拟手续费重跑，权益变化会影响后续仓位；未伪装为固定曲线扣费。
没有历史资金费率、盘口与真实滑点、逐日合约规则；数据缺口不补造，缺口内部风险不可观察。
原版与候选的止损和定仓机制不同，不能把两者收益差全部归因于一个参数。

[完整106项指标](comparison.csv) · [判定](decision.json) · [冻结清单](manifest.json) · [源码归档](frozen_sources.zip) · [复核说明](README.md)
"""
    (ROOT / "RESULTS.md").write_text(text)
    html = """<!doctype html><html lang="zh"><meta charset="utf-8"><title>趋势空头稳定域检验</title>
<style>body{font:16px system-ui;max-width:1200px;margin:40px auto;padding:0 20px;color:#172331}img{max-width:100%}table{border-collapse:collapse;font-size:13px}th,td{padding:8px;border:1px solid #ddd}th{background:#f1f4f7}p{line-height:1.7}.scroll{overflow:auto}</style>"""
    html += f"<h1>趋势空头参数稳定域</h1><p>{conclusion}</p><p>106次原生回测，2021-01-01至2026-08-30；已见历史诊断。蓝框为冻结中心。费用压力已重新定仓。</p>"
    html += (
        f"<p>满足预定成本、时间和笔数条件的格点：BTC {decision['eligible_counts']['BTC']}/25，"
        f"ETH {decision['eligible_counts']['ETH']}/25；共同格点 {decision['common_eligible_count']} 个。</p>"
        "<p>横轴为锁利比例，纵轴为原止损ATR系数的倍数。成本压力为每次成交额外5bp（0.05%）。"
        "未模拟历史资金费率、真实滑点和逐日历史合约规则。</p>"
    )
    for name in ("heatmap_full.png", "heatmap_recent.png", "annual_BTC.png", "annual_ETH.png"):
        html += f'<img src="{name}" alt="参数热力图">'
    html += '<p><a href="RESULTS.md">完整中文报告与限制</a> · <a href="comparison.csv">下载全部指标</a></p>'
    html += (
        '<div class="scroll">'
        + frame[
            ["id", "return_pct", "max_drawdown_pct", "profit_factor", "trades", "review_marked_pnl"]
        ].to_html(index=False, float_format=lambda v: f"{v:.3f}")
        + "</div></html>"
    )
    (ROOT / "report.html").write_text(html)
    print(conclusion, decision, flush=True)


if __name__ == "__main__":
    main()
