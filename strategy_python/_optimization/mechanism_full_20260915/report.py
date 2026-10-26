"""Verify and summarize frozen-parameter BTC historical replay, including training."""

import hashlib
import json
import os
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parents[2]
LABELS = {
    "BTC_MRL_original": "均值回归2σ",
    "BTC_MRL_optimized": "均值回归2.25σ",
    "BTC_TFL": "趋势多头",
    "BTC_TFS": "趋势空头",
    "BTC_PBS": "反弹空头",
}


def main():
    cases = json.loads((ROOT / "cases.json").read_text())
    assert len(cases) == 7
    old_sources = json.loads(
        (ROOT.parent / "mechanism_20260915/frozen_runtime_sources.json").read_text()
    )
    for path, digest in old_sources.items():
        assert hashlib.sha256((PROJECT / path).read_bytes()).hexdigest() == digest, path
    copies = json.loads((ROOT / "copy_verification.json").read_text())
    for src, info in copies.items():
        assert hashlib.sha256((PROJECT / src).read_bytes()).hexdigest() == info["source_sha256"]
        assert (
            hashlib.sha256((PROJECT / info["copy"]).read_bytes()).hexdigest() == info["copy_sha256"]
        )
    assert json.loads((ROOT / "parity_verification.json").read_text())["fills_identical"]
    audit = json.loads((ROOT.parent / "mechanism_20260915/BTC_data_audit.json").read_text())
    for source, digest in audit["source_hashes"].items():
        assert hashlib.sha256((PROJECT / source).read_bytes()).hexdigest() == digest, source
    rows = []
    annual = []
    lookup = {}
    sources = {}
    for key, variant in cases:
        path = ROOT / "runs/full" / (key + ("__" + variant if variant != "main" else ""))
        params = json.loads((path / "parameters.json").read_text())
        m = json.loads((path / "metrics.json").read_text())
        fills = pd.read_csv(path / "fills.csv")
        times = pd.to_datetime(fills.ts_last, utc=True)
        assert (times >= pd.Timestamp("2021-01-01", tz="UTC")).all()
        assert (times < pd.Timestamp("2026-08-31", tz="UTC")).all()
        assert params["training_overlap"] is True
        assert params["starting_balance"] == 500 and params["account_leverage"] == 5
        assert params["start"] == "2021-01-01T00:00:00+00:00"
        assert params["end"] == "2026-08-31T00:00:00+00:00"
        assert params["runner_sha256"] == hashlib.sha256((ROOT / "run.py").read_bytes()).hexdigest()
        for source, digest in params["source_sha256"].items():
            assert hashlib.sha256((PROJECT / source).read_bytes()).hexdigest() == digest, source
        assert (
            m["off_tick_order_price_fields"] == 0 and abs(m["cashflow_reconciliation_error"]) < 1e-4
        )
        assert m["missing_minutes_including_edges"] == 938
        assert abs(sum(m["annual_marked_pnl"].values()) - m["net_pnl"]) < 1e-4
        if key == "BTC_PBS":
            assert (
                params["config"]["risk_fraction"] == 0.015
                and params["config"]["max_notional_fraction"] == 3
            )
        sources[str(path.relative_to(PROJECT))] = {
            "metrics_sha256": hashlib.sha256((path / "metrics.json").read_bytes()).hexdigest(),
            "parameters_sha256": hashlib.sha256(
                (path / "parameters.json").read_bytes()
            ).hexdigest(),
        }
        row = {
            "key": key,
            "variant": variant,
            "label": LABELS[key] + ("（常数风险）" if variant == "flat_risk" else ""),
            "net_pnl": m["net_pnl"],
            "return_pct": m["return_pct"],
            "max_drawdown_pct": m["max_drawdown_pct"],
            "trades": m["cashflow_trades"],
            "win_rate_pct": m["cashflow_win_rate_pct"],
            "fees": m["fees"],
            "extra_5bp_net_pnl": m["stress"]["5"]["net_pnl"],
        }
        row["last_fill_utc"] = str(times.max())
        row["fills_2025"] = int((times.dt.year == 2025).sum())
        row["fills_2026"] = int((times.dt.year == 2026).sum())
        rows.append(row)
        lookup[key, variant] = row
        annual.append(
            {"key": key, "variant": variant, "label": row["label"], **m["annual_marked_pnl"]}
        )
        family = key.removeprefix("BTC_").split("_")[0]
        for version, folder in [
            ("fixed", "btc_transfer_20260914"),
            ("scaled", "btc_scale_20260915"),
        ]:
            for suffix in ["original", "optimized"]:
                bkey = family + "_" + suffix
                if family == "PBS" and suffix == "original":
                    bkey = "PBS_original_matched"
                base = ROOT.parent / folder / "runs/full" / bkey
                bp = json.loads((base / "parameters.json").read_text())
                bm = json.loads((base / "metrics.json").read_text())
                for field in [
                    "start",
                    "end",
                    "starting_balance",
                    "maker_fee",
                    "taker_fee",
                    "price_increment",
                    "size_increment",
                ]:
                    assert bp[field] == params[field], (key, field)
                row[version + "_" + suffix + "_net_pnl"] = bm["net_pnl"]
    pd.DataFrame(rows).to_csv(ROOT / "comparison.csv", index=False)
    pd.DataFrame(annual).to_csv(ROOT / "annual.csv", index=False)
    (ROOT / "results.json").write_text(
        json.dumps({"summary": rows, "annual": annual}, ensure_ascii=False, indent=2) + "\n"
    )
    lines = [
        "# 最新新机制版：BTC从2021年起的完整历史回放",
        "",
        "区间2021-01-01至2026-08-30 UTC，各配置独立500 USDT、连续持仓与资金、1分钟数据、保证金杠杆5倍。手续费已计入；未模拟真实滑点、资金费率和逐日历史合约规则。",
        "",
        "**沿用2021—2023训练得到的冻结参数，2021—2023属于训练期重放，含训练信息；不是滚动训练，也不能称为2021起的样本外验证。** 2024以后参数未再拟合，但本次账户持仓/权益承接早期历史，不能与2024或2025独立起始回测直接拼接。",
        "",
        "## 完整区间表现",
        "",
        "| 新机制配置 | 净收益USDT | 收益率 | 最大回撤 | 交易数 | 手续费USDT |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        lines.append(
            f"| {row['label']} | {row['net_pnl']:.2f} | {row['return_pct']:.2f}% | {row['max_drawdown_pct']:.2f}% | {row['trades']} | {row['fees']:.2f} |"
        )
    lines += [
        "",
        "## 年度净收益：同一连续账户",
        "",
        "| 配置 | 2021 | 2022 | 2023 | 2024 | 2025 | 2026截至8月30日 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in annual:
        lines.append(
            "| "
            + row["label"]
            + " | "
            + " | ".join(f"{row.get(str(y), 0):.2f}" for y in range(2021, 2027))
            + " |"
        )
    lines += [
        "",
        "年度金额USDT，按年末盯市权益变化计算，不是每年重置本金。",
        "",
        "## 同区间旧版本对照",
        "",
        "| 新机制配置 | 固定原版 | 固定优化版 | 缩放原版 | 缩放优化版 | 新机制 |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        lines.append(
            "| "
            + row["label"]
            + " | "
            + " | ".join(
                f"{row[k]:.2f}"
                for k in [
                    "fixed_original_net_pnl",
                    "fixed_optimized_net_pnl",
                    "scaled_original_net_pnl",
                    "scaled_optimized_net_pnl",
                    "net_pnl",
                ]
            )
            + " |"
        )
    lines += [
        "",
        "反弹空头全部对齐1.5%风险/3倍名义上限，旧原/优化同预算下相同。MRL两种新机制保留不同入场布林倍数，原/优化对照列是各自历史配置，不能视作只改变单一参数。趋势候选同时改变仓位风险控制，MRL候选包含订单快照修正，差异不能全归因于价格尺度。",
        "",
        "## 结果解读",
        "",
        "五个主配置仅趋势空头盈利：净收益22.31 USDT、回撤3.84%；额外每侧5bp成本后净收益降至5.02 USDT。趋势多头较旧版少亏，但仍亏损；均值回归和反弹空头没有改善完整历史结果。",
        "",
        "常数风险2σ版净赚11.13 USDT，但回撤32.70%，每侧额外2bp成本就转亏。2.25σ版仍亏78.60 USDT，最后成交在2024-09-08，2025/2026没有成交，后段平坦不能解释为持续交易下的稳定盈利。完整历史不支持把常数风险或新机制整体视为稳健优化。",
        "",
        "## 核验和数据",
        "",
        "7项回放成交现金流与权益一致，订单价格符合步长。副本52文件仅修改导入路径和日期准入，交易逻辑AST反向核对一致；同区间原生成交一致性验证通过。原有335文件冻结清单未变。全历史缺938分钟，未填造价格。回撤按分钟收盘盯市。",
        "",
        "[完整CSV和成本压力](comparison.csv) · [年度CSV](annual.csv) · [核验清单](verification.json) · [复现入口](README.md) · [此前2024以后研究](../mechanism_20260915/CONCLUSIONS.md)",
        "",
        "额外每侧5bp成本压力使用固定成交路径扣费，不会按扣费后权益重新定仓。",
    ]
    lines += ["", "![连续资金曲线：橙色背景为训练重叠区间](equity.png)"]
    (ROOT / "RESULTS.md").write_text("\n".join(lines) + "\n")
    (ROOT / "verification.json").write_text(
        json.dumps(
            {
                "runs": len(rows),
                "copies_verified": len(copies),
                "old_frozen_files_unchanged": len(old_sources),
                "cashflows_reconciled": True,
                "prices_on_tick": True,
                "missing_minutes": 938,
                "includes_training": True,
                "market_files_verified": len(audit["source_hashes"]),
                "sources": sources,
            },
            indent=2,
        )
        + "\n"
    )
    os.environ.setdefault("MPLCONFIGDIR", str(PROJECT / ".local/cache/matplotlib"))
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(2, 2, figsize=(13, 7), layout="constrained")
    for ax, family in zip(axes.flat, ["MRL", "TFL", "TFS", "PBS"]):
        slot = "MRL_optimized" if family == "MRL" else family
        paths = [
            (ROOT / "runs/full" / ("BTC_" + slot), "Mechanism"),
            (
                ROOT.parent / "btc_scale_20260915/runs/full" / (family + "_optimized"),
                "Scaled optimized",
            ),
        ]
        if family == "MRL":
            paths.append((ROOT / "runs/full/BTC_MRL_optimized__flat_risk", "Flat risk"))
        for path, label in paths:
            d = pd.read_csv(path / "daily_equity.csv", index_col=0, parse_dates=True)
            ax.plot(d.index, (d.equity / 500 - 1) * 100, label=label, lw=1)
        ax.axvspan(
            pd.Timestamp("2021-01-01", tz="UTC"),
            pd.Timestamp("2024-01-01", tz="UTC"),
            alpha=0.06,
            color="orange",
            label="Calibration overlap",
        )
        ax.set_title("BTC " + family)
        ax.set_ylabel("Return (%)")
        ax.grid(alpha=0.2)
        ax.legend(fontsize=8)
        ax.tick_params(axis="x", rotation=20)
    fig.savefig(ROOT / "equity.png", dpi=140)
    plt.close(fig)
    print("REPORT_VERIFIED", len(rows))


if __name__ == "__main__":
    main()
