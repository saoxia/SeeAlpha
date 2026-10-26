"""Formal evidence report; fail closed when any declared run is missing/stale."""

import functools
import hashlib
import json
import operator
import os
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parents[2]
LABELS = {
    "MRL_original": "均值回归2σ",
    "MRL_optimized": "均值回归2.25σ",
    "TFL": "趋势多头",
    "TFS": "趋势空头",
    "PBS": "反弹空头",
}


def main():
    verification = json.loads((ROOT / "runtime_verification.json").read_text())
    assert len(verification["stress_runs"]) == 14
    assert verification["family_streaming_parity"] == 4
    assert verification["all_prices_on_tick"] and verification["all_cashflows_reconciled"]
    registry = json.loads((ROOT / "runtime_strategies.json").read_text())
    calibration = json.loads((ROOT / "candidate_coefficients.json").read_text())
    assert calibration == json.loads(
        (PROJECT / "strategy_python/_mechanism/coefficients.json").read_text()
    )
    assert (
        calibration["definition_sha256"]
        == hashlib.sha256((ROOT / "CANDIDATE_DEFINITION.md").read_bytes()).hexdigest()
    )
    market_files = 0
    for asset in ("ETH", "BTC"):
        audit = json.loads((ROOT / f"{asset}_data_audit.json").read_text())
        assert (
            audit["cache_sha256"]
            == hashlib.sha256((ROOT / f"{asset}_minutes.parquet").read_bytes()).hexdigest()
        )
        for path, digest in audit["source_hashes"].items():
            assert hashlib.sha256((PROJECT / path).read_bytes()).hexdigest() == digest, path
            market_files += 1
    frozen = json.loads((ROOT / "frozen_runtime_sources.json").read_text())
    for path, digest in frozen.items():
        assert hashlib.sha256((PROJECT / path).read_bytes()).hexdigest() == digest, path
    cases = functools.reduce(
        operator.iadd,
        [json.loads((ROOT / f"{stage}_cases.json").read_text()) for stage in ("main", "neighbors")],
        [],
    )
    assert len(cases) == 112, len(cases)
    rows, order_events = [], []
    market_conditions = {}
    expected = {}
    for asset in ("ETH", "BTC"):
        frame = pd.read_parquet(ROOT / f"{asset}_minutes.parquet", columns=["close"])
        for period, start, end in (
            ("validation", "2024-01-01", "2025-01-01"),
            ("review", "2025-01-01", "2026-08-31"),
        ):
            # Index is minute END; source event is one millisecond before it.
            start, end = pd.Timestamp(start, tz="UTC"), pd.Timestamp(end, tz="UTC")
            observed = ((frame.index > start) & (frame.index <= end)).sum()
            expected[asset, period] = int((end - start).total_seconds() / 60 - observed)
    for key, period, variant, mode in cases:
        info = registry[key]
        directory = ROOT / "runs" / period / (key + ("__" + variant if variant != "main" else ""))
        metric = json.loads((directory / "metrics.json").read_text())
        params = json.loads((directory / "parameters.json").read_text())
        start, end = (
            ("2024-01-01", "2025-01-01") if period == "validation" else ("2025-01-01", "2026-08-31")
        )
        assert pd.Timestamp(params["start"]) == pd.Timestamp(start, tz="UTC")
        assert pd.Timestamp(params["end"]) == pd.Timestamp(end, tz="UTC")
        assert params["config"]["instrument_id"] == f"{info['asset']}USDT-PERP.BINANCE"
        conditions = {
            name: params[name]
            for name in (
                "maker_fee",
                "taker_fee",
                "price_increment",
                "size_increment",
                "min_notional",
                "nautilus_version",
            )
        }
        if info["asset"] in market_conditions:
            assert market_conditions[info["asset"]] == conditions
        else:
            market_conditions[info["asset"]] = conditions
        if info["slot"].startswith("PBS"):
            assert params["config"]["risk_fraction"] == 0.015
            assert params["config"]["max_notional_fraction"] == 3
        fills = pd.read_csv(directory / "fills.csv")
        orphan_exits = (
            int(fills.tags.fillna("").str.contains("late_add_after_flat", regex=False).sum())
            if "tags" in fills
            else 0
        )
        assert params["runner_sha256"] == hashlib.sha256((ROOT / "run.py").read_bytes()).hexdigest()
        for path, digest in params["source_sha256"].items():
            assert frozen[path] == digest, (key, path)
        assert params["variant"] == variant and params["mode"] == mode
        assert params["starting_balance"] == 500 and params["account_leverage"] == 5
        assert metric["missing_minutes_including_edges"] == expected[info["asset"], period]
        assert metric["off_tick_order_price_fields"] == 0
        assert abs(metric.get("cashflow_reconciliation_error", 0)) < 0.0001
        rows.append(
            {
                "key": key,
                "asset": info["asset"],
                "kind": info["kind"],
                "slot": info["slot"],
                "period": period,
                "variant": variant,
                **{
                    name: metric.get(name)
                    for name in (
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
                "extra_5bp_pnl": metric["stress"]["5"]["net_pnl"],
                "expected_missing_minutes": expected[info["asset"], period],
                "late_add_flatten_fills": orphan_exits,
            }
        )
        for event, count in metric["order_event_reasons"].items():
            order_events.append(
                {"key": key, "period": period, "variant": variant, "event": event, "count": count}
            )
    frame = pd.DataFrame(rows)
    frame.to_json(ROOT / "formal_comparison.json", orient="records", indent=2, force_ascii=False)
    frame.drop(columns=["annual_marked_pnl"]).to_csv(ROOT / "formal_comparison.csv", index=False)
    pd.DataFrame(order_events).to_csv(ROOT / "formal_order_events.csv", index=False)
    lookup = {(row["key"], row["period"], row["variant"]): row for row in rows}
    lines = [
        "# ETH/BTC参数机制研究：正式回测",
        "",
        "新方案用信号后的条件路径分布定义价格距离，再在每次入场冻结当前ATR或布林带尺度。原始经验值的真实成因不能仅靠回测反推出；本报告区分候选是否有用、统计解释是否得到支持。",
        "",
        "训练2021—2023；2024为检验，2025-01-01至2026-08-30为历史复核。后续历史此前已看过，并非全新盲测。各回测独立500USDT，1分钟数据、保证金杠杆5倍、90天分批连续运行。PBS全部统一1.5%风险/3倍名义上限。",
        "",
        "[完整数值](formal_comparison.csv) · [年度明细](formal_comparison.json) · [机制定义](CANDIDATE_DEFINITION.md) · [统计诊断](DIAGNOSTICS.md) · [恢复事件证据](RECOVERY_EVIDENCE.md) · [跨时期统计含义](MECHANISM_EVIDENCE.md) · [成交盈亏结构](TRADE_PAYOFF_EVIDENCE.md)",
        "",
    ]
    for asset in ("ETH", "BTC"):
        for period, title in (("validation", "2024检验"), ("review", "2025年以来历史复核")):
            lines += [
                f"## {asset} {title}",
                "",
                "| 策略 | 旧原版净收益 | 旧优化版净收益 | 新机制净收益 | 新机制回撤 | 新机制交易数 |",
                "|---|---:|---:|---:|---:|---:|",
            ]
            for slot, label in LABELS.items():
                family = slot.split("_")[0]
                a = lookup[f"fixed_{asset}_{family}_original", period, "main"]
                b = lookup[f"fixed_{asset}_{family}_optimized", period, "main"]
                c = lookup[f"{asset}_{slot}", period, "main"]
                lines.append(
                    f"| {label} | {a['net_pnl']:.2f} | {b['net_pnl']:.2f} | {c['net_pnl']:.2f} | {c['max_drawdown_pct']:.2f}% | {c['cashflow_trades']} |"
                )
            lines += [
                "",
                "金额USDT。MRL两行分别表示不同BOLL入场条件；趋势旧两版的不同启动百分比被同一机制定义替代，所以每个方向只有一个机制候选。",
                "",
            ]
    lines += [
        "## BTC相对前次波动比例方案",
        "",
        "| 策略 | 区间 | 比例原版净收益 | 比例优化版净收益 | 新机制净收益 |",
        "|---|---|---:|---:|---:|",
    ]
    for period in ("validation", "review"):
        for slot, label in LABELS.items():
            family = slot.split("_")[0]
            a = lookup[f"scaled_BTC_{family}_original", period, "main"]
            b = lookup[f"scaled_BTC_{family}_optimized", period, "main"]
            c = lookup[f"BTC_{slot}", period, "main"]
            lines.append(
                f"| {label} | {period} | {a['net_pnl']:.2f} | {b['net_pnl']:.2f} | {c['net_pnl']:.2f} |"
            )
    lines += [
        "",
        "## Q1/Q2：去掉复杂风险曲线",
        "",
        "常数乘数来自训练机会中原强度×位置系数的平均值，恢复系数仍保留。它是平均预算近似匹配，并不保证各成交的预算相同。",
        "",
        "| 资产/信号 | 区间 | 原曲线净收益/回撤 | 常数乘数净收益/回撤 |",
        "|---|---|---:|---:|",
    ]
    for asset in ("ETH", "BTC"):
        for slot in ("MRL_original", "MRL_optimized"):
            for period in ("validation", "review"):
                a = lookup[f"{asset}_{slot}", period, "main"]
                b = lookup[f"{asset}_{slot}", period, "flat_risk"]
                lines.append(
                    f"| {asset}/{slot} | {period} | {a['net_pnl']:.2f} / {a['max_drawdown_pct']:.2f}% | {b['net_pnl']:.2f} / {b['max_drawdown_pct']:.2f}% |"
                )
    lines += [
        "",
        "## 预声明邻域",
        "",
        "主方案没有根据这些结果修改。趋势分别检查止损70%/90%及启动40%/60%分位；PBS检查90%缓冲，70%缓冲与主80%系数相同，不重复运行。",
        "",
        "| 资产/策略 | 区间 | 主方案净收益 | 邻域最低—最高净收益 | 邻域最低—最高回撤 |",
        "|---|---|---:|---:|---:|",
    ]
    for asset in ("ETH", "BTC"):
        for slot in ("TFL", "TFS", "PBS"):
            for period in ("validation", "review"):
                group = frame[
                    (frame.key == f"{asset}_{slot}")
                    & (frame.period == period)
                    & (frame.variant != "main")
                ]
                center = lookup[f"{asset}_{slot}", period, "main"]
                lines.append(
                    f"| {asset}/{slot} | {period} | {center['net_pnl']:.2f} | {group.net_pnl.min():.2f}—{group.net_pnl.max():.2f} | {group.max_drawdown_pct.min():.2f}%—{group.max_drawdown_pct.max():.2f}% |"
                )
    lines += [
        "",
        "## 成本与解释限制",
        "",
        "成交手续费已计入；资金费率、真实滑点、历史逐日合约规则未重演。每侧额外5bp成本压力列在CSV中，沿用固定成交路径，没有按扣费后权益重新定仓。回撤按分钟收盘盯市，未覆盖分钟内极值。",
        "",
        "趋势候选同时按止损距离调整名义仓位以限制计划价格风险。因此收益/回撤差异包含退出和仓位共同影响，不能全部归因于止损参数。MRL候选还修复了改单参数确认的生命周期，不能把所有表现差异归为统计尺度。",
        "",
        "公共机会标签忽略原持仓互斥、恢复等待和重入状态，仅用于估计；正式回测保留这些运行限制。80%等分位门槛属于研究设计，不是发现的市场常数。Q3精细恢复时间带样本不足，保留未知结论。",
        "",
        "112项正式回测与14项运行验证已完成；源码、4868个原始行情文件、冻结参数定义、成交账目和价格步长均已核对。",
    ]
    (ROOT / "FORMAL_RESULTS.md").write_text("\n".join(lines) + "\n")
    os.environ.setdefault("MPLCONFIGDIR", str(PROJECT / ".local/cache/matplotlib"))
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(2, 4, figsize=(16, 7), layout="constrained")
    for row, asset in enumerate(("ETH", "BTC")):
        for col, family in enumerate(("MRL", "TFL", "TFS", "PBS")):
            ax = axes[row, col]
            slot = "MRL_optimized" if family == "MRL" else family
            for key, label in (
                (f"fixed_{asset}_{family}_optimized", "Old optimized"),
                (f"{asset}_{slot}", "Mechanism"),
            ):
                daily = pd.read_csv(
                    ROOT / "runs/review" / key / "daily_equity.csv", index_col=0, parse_dates=True
                )
                ax.plot(daily.index, (daily.equity / 500 - 1) * 100, label=label, lw=1)
            ax.set_title(f"{asset} {family}")
            ax.grid(alpha=0.15)
            ax.set_ylabel("Return (%)")
            ax.tick_params(axis="x", rotation=30)
            ax.legend(fontsize=7, frameon=False)
    fig.savefig(ROOT / "mechanism_equity.png", dpi=150)
    plt.close(fig)
    (ROOT / "formal_verification.json").write_text(
        json.dumps(
            {
                "runs_verified": len(rows),
                "stress_runs_verified": len(verification["stress_runs"]),
                "family_streaming_parity": verification["family_streaming_parity"],
                "all_prices_on_tick": True,
                "all_cashflows_reconciled": True,
                "sources_verified": True,
                "market_source_files_verified": market_files,
                "calibration_definition_verified": True,
                "missing_minutes": {f"{a}_{p}": n for (a, p), n in expected.items()},
            },
            indent=2,
        )
        + "\n"
    )
    print("FORMAL_REPORT_COMPLETE", len(rows))


if __name__ == "__main__":
    main()
