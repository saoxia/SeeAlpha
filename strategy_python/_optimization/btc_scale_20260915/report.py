"""Compare frozen BTC scale runs with frozen fixed-parameter BTC baselines."""

import hashlib
import json
import os
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parents[2]
BASE = ROOT.parent / "btc_transfer_20260914"
LABELS = {"MRL": "均值回归多头", "TFL": "趋势多头", "TFS": "趋势空头", "PBS": "小时反弹空头"}


def main():
    strategies = json.loads((ROOT / "strategies.json").read_text())
    data = json.loads((BASE / "data_audit.json").read_text())
    rows, events = [], []
    for directory, kind, manifest in (
        (BASE, "fixed", "frozen_btc_hashes.json"),
        (ROOT, "scaled", "frozen_scaled_hashes.json"),
    ):
        frozen = json.loads((directory / manifest).read_text())
        for path, digest in frozen.items():
            assert hashlib.sha256((PROJECT / path).read_bytes()).hexdigest() == digest, path
        for period in ("full", "recent"):
            for key in [*strategies, "PBS_original_matched"]:
                run = directory / "runs" / period / key
                m = json.loads((run / "metrics.json").read_text())
                p = json.loads((run / "parameters.json").read_text())
                if kind == "scaled":
                    reference = json.loads(
                        (BASE / "runs" / period / key / "parameters.json").read_text()
                    )
                    for field in (
                        "start",
                        "end",
                        "batch_days",
                        "mode",
                        "maker_fee",
                        "taker_fee",
                        "price_increment",
                        "size_increment",
                        "min_notional",
                        "starting_balance",
                        "account_leverage",
                        "warmup_days",
                        "nautilus_version",
                    ):
                        assert p[field] == reference[field], (key, period, field)
                assert (
                    p["runner_sha256"]
                    == hashlib.sha256((directory / "run.py").read_bytes()).hexdigest()
                )
                assert (
                    m["missing_minutes_including_edges"]
                    == data["periods"][period]["missing_minutes"]
                )
                assert m["off_tick_order_price_fields"] == 0
                assert abs(m["cashflow_reconciliation_error"]) < 0.0001
                for path, digest in p["source_sha256"].items():
                    assert frozen[path] == digest
                rows.append(
                    {
                        "kind": kind,
                        "period": period,
                        "key": key,
                        **{
                            k: m[k]
                            for k in (
                                "net_pnl",
                                "return_pct",
                                "max_drawdown_pct",
                                "cashflow_trades",
                                "cashflow_win_rate_pct",
                                "payoff_ratio",
                                "fees",
                                "turnover_usdt",
                                "annual_marked_pnl",
                            )
                        },
                        "extra_5bp_pnl": m["stress"]["5"]["net_pnl"],
                    }
                )
                for reason, count in m["order_event_reasons"].items():
                    events.append(
                        {
                            "kind": kind,
                            "period": period,
                            "key": key,
                            "event": reason,
                            "count": count,
                        }
                    )
        for period in ("full", "recent"):
            cols = ["side", "filled_qty", "avg_px", "commissions", "ts_last"]
            pd.testing.assert_frame_equal(
                pd.read_csv(directory / "runs" / period / "PBS_original_matched/fills.csv")[cols],
                pd.read_csv(directory / "runs" / period / "PBS_optimized/fills.csv")[cols],
            )
    assert len(rows) == 36
    frame = pd.DataFrame(rows)
    frame.to_json(ROOT / "comparison.json", orient="records", indent=2, force_ascii=False)
    frame.drop(columns="annual_marked_pnl").to_csv(ROOT / "comparison.csv", index=False)
    pd.DataFrame(events).to_csv(ROOT / "order_audit.csv", index=False)
    lookup = {(r["kind"], r["period"], r["key"]): r for r in rows}
    lines = [
        "# BTC尺度适配完整回测",
        "",
        "比较同一BTC行情下的固定ETH参数版与BTC尺度适配版。尺度系数固定为0.7363034074003296，未根据本次收益修改。",
        "",
        "每个策略独立500 USDT，1分钟行情、账户保证金杠杆5倍；全区间UTC [2021-01-01,2026-08-31)，近期 [2025-01-01,2026-08-31)，实际截止2026-08-30。近期独立从500开始，不能直接与全区间的年度收益相加。",
        "",
        "[详细CSV](comparison.csv) · [年度及费用数据](comparison.json) · [订单事件](order_audit.csv) · [尺度依据](../../BTC_SCALE.md)",
        "",
    ]
    for period, title in (("full", "全历史"), ("recent", "近期")):
        lines += [
            f"## {title}：固定尺度与BTC尺度",
            "",
            "| 策略 | 固定净收益USDT | 适配净收益USDT | 收益变化USDT | 固定回撤 | 适配回撤 | 适配交易数 |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
        improved = safer = both = positive = 0
        for key, info in strategies.items():
            a, b = lookup["fixed", period, key], lookup["scaled", period, key]
            improved += b["net_pnl"] > a["net_pnl"]
            safer += b["max_drawdown_pct"] < a["max_drawdown_pct"]
            both += b["net_pnl"] > a["net_pnl"] and b["max_drawdown_pct"] < a["max_drawdown_pct"]
            positive += b["net_pnl"] > 0
            label = LABELS[info["family"]] + ("原版" if info["version"] == "original" else "优化版")
            lines += [
                f"| {label} | {a['net_pnl']:.2f} | {b['net_pnl']:.2f} | {b['net_pnl'] - a['net_pnl']:+.2f} | {a['max_drawdown_pct']:.2f}% | {b['max_drawdown_pct']:.2f}% | {b['cashflow_trades']} |"
            ]
        lines += [
            "",
            f"8个默认版本中，{improved}个净收益改善、{safer}个回撤下降、{both}个两者同时改善；适配后{positive}个净收益为正。此处改善也可能只是亏损变小。",
            "",
        ]
    lines += [
        "## BTC尺度下，优化版是否优于原版",
        "",
        "| 家族 | 全历史原版净收益 | 全历史优化版净收益 | 近期原版净收益 | 近期优化版净收益 |",
        "|---|---:|---:|---:|---:|",
    ]
    for family, label in LABELS.items():
        old = family + ("_original_matched" if family == "PBS" else "_original")
        vals = [
            lookup["scaled", period, key]["net_pnl"]
            for period in ("full", "recent")
            for key in (old, family + "_optimized")
        ]
        lines += [
            f"| {label}{'（同预算）' if family == 'PBS' else ''} | "
            + " | ".join(f"{v:.2f}" for v in vals)
            + " |"
        ]
    lines += [
        "",
        "PBS原版默认风险0.5%/名义上限1倍，优化版1.5%/3倍。同预算时，两版在固定与适配尺度、两个区间下的成交方向、数量、价格、费用和时点全部一致；其默认差异来自预算。",
        "",
        "## 费用压力",
        "",
        "| 策略 | 适配全历史净收益 | 额外每侧5bp后的净收益 |",
        "|---|---:|---:|",
    ]
    for key in strategies:
        m = lookup["scaled", "full", key]
        lines += [f"| {key} | {m['net_pnl']:.2f} | {m['extra_5bp_pnl']:.2f} |"]
    lines += [
        "",
        "额外5bp是在已生成的成交路径上扣减成本，没有按变化后的权益重新定仓或重演成交。",
        "",
        "## 验证与解释边界",
        "",
        "18个新增回测和18个历史对照均检查源码/运行器哈希、数据缺口、成交现金流对账及最终订单价格步长。全区间缺938分钟，近期缺18分钟；沿用相同数据，没有补造行情。撤单/改单失败等原始原因保存在订单事件表，不能把它们一概解释为开仓拒绝。",
        "",
        "手续费计入；未计资金费率及真实滑点，采用当前合约规则快照而非历史逐日交易规则。回撤按分钟收盘盯市，未覆盖分钟内极值。独立账户结果不能直接相加当成共享账户组合结果。",
        "",
        "统一波动比只改变价格距离，不能保证保留尾部概率和不同持仓周期的波动特征。原始ETH参数和BTC历史均已被研究者查看；这次结果可检验尺度迁移表现，不能单独证明或排除过拟合，也不能视为全新盲测。",
        "",
        "![完整历史资金曲线](equity_comparison.png)",
    ]
    (ROOT / "RESULTS.md").write_text("\n".join(lines) + "\n")
    os.environ.setdefault("MPLCONFIGDIR", str(PROJECT / ".local/cache/matplotlib"))
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(2, 2, figsize=(14, 8), layout="constrained")
    for ax, family in zip(axes.flat, LABELS):
        for kind, directory, style in (("fixed", BASE, "--"), ("scaled", ROOT, "-")):
            for version, color in (("original", "#c17835"), ("optimized", "#2366b1")):
                daily = pd.read_csv(
                    directory / "runs/full" / f"{family}_{version}" / "daily_equity.csv",
                    index_col=0,
                    parse_dates=True,
                )
                ax.plot(
                    daily.index,
                    (daily.equity / 500 - 1) * 100,
                    linestyle=style,
                    color=color,
                    label=f"{kind} {version}",
                    lw=1,
                )
        ax.set_title(family + (" (different default risk budgets)" if family == "PBS" else ""))
        ax.set_ylabel("Return (%)")
        ax.axhline(0, color="gray", lw=0.6)
        ax.grid(alpha=0.15)
        ax.legend(frameon=False, fontsize=8)
    fig.suptitle("BTC | fixed versus scaled | 500 USDT per strategy | through 2026-08-30")
    fig.savefig(ROOT / "equity_comparison.png", dpi=150)
    plt.close(fig)
    (ROOT / "full_verification.json").write_text(
        json.dumps(
            {
                "new_runs": 18,
                "baseline_runs": 18,
                "hashes_verified": True,
                "cashflow_reconciled": True,
                "all_final_prices_on_tick": True,
                "matched_pbs_fills_equal": True,
            },
            indent=2,
        )
        + "\n"
    )
    print("REPORT_COMPLETE")


if __name__ == "__main__":
    main()
