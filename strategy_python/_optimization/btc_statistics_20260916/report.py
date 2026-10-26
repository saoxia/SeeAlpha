"""Summarize all BTC packages and available runs without selecting winners."""

import hashlib
import json
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parents[2]
OPT = ROOT.parent


def read_run(path):
    m = json.loads((path / "metrics.json").read_text())
    p = (
        json.loads((path / "parameters.json").read_text())
        if (path / "parameters.json").exists()
        else {}
    )
    for source, expected in p.get("source_sha256", {}).items():
        assert hashlib.sha256((PROJECT / source).read_bytes()).hexdigest() == expected, source
    return {
        "run": str(path.relative_to(PROJECT)),
        "start": p.get("start", "2021-01-01T00:00:00+00:00"),
        "end_exclusive": p.get("end", "2026-08-31T00:00:00+00:00"),
        "starting_balance": p.get("starting_balance", 500),
        "net_pnl": m["net_pnl"],
        "return_pct": m["return_pct"],
        "max_drawdown_pct": m["max_drawdown_pct"],
        "win_rate_pct": m.get("cashflow_win_rate_pct", m.get("win_rate_pct")),
        "payoff_ratio": m.get("payoff_ratio", m.get("payoff")),
        "trades": m.get("cashflow_trades", m.get("trades")),
        "fees": m.get("fees"),
        "maker_fee": p.get("maker_fee", "not_applicable"),
        "taker_fee": p.get("taker_fee", 0.0005),
        "risk_fraction": p.get("config", {}).get("risk_fraction"),
        "max_notional_fraction": p.get("config", {}).get("max_notional_fraction"),
    }


def markdown_table(frame):
    lines = [
        "| 版本 | 净收益USDT | 收益率 | 最大回撤 | 胜率 | 盈亏比 | 笔数 | 手续费USDT |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in frame.to_dict("records"):
        payoff = f"{r['payoff_ratio']:.4f}" if pd.notna(r["payoff_ratio"]) else "—"
        lines.append(
            f"| {r['slot']} | {r['net_pnl']:.2f} | {r['return_pct']:.2f}% | {r['max_drawdown_pct']:.2f}% | {r['win_rate_pct']:.2f}% | {payoff} | {int(r['trades'])} | {r['fees']:.2f} |"
        )
    return lines


def main():
    rows = []
    for study, group in [
        ("btc_transfer_20260914", "初始BTC迁移版"),
        ("btc_scale_20260915", "波动尺度适配版"),
    ]:
        registry = json.loads((OPT / study / "strategies.json").read_text())
        for slot, info in registry.items():
            rows.append(
                {
                    "group": group,
                    "slot": slot,
                    "package": "strategy_python/" + info["package"].replace(".", "/"),
                    "engine": "Nautilus",
                    **read_run(OPT / study / "runs/full" / slot),
                }
            )
    mechanisms = json.loads((PROJECT / "strategy_python/_mechanism/strategies.json").read_text())
    for slot, info in mechanisms.items():
        rows.append(
            {
                "group": "机制重构版",
                "slot": slot,
                "package": "strategy_python/" + info["package"],
                "engine": "Nautilus_historical_copy",
                **read_run(OPT / "mechanism_full_20260915/runs/full" / ("BTC_" + slot)),
            }
        )
    profiles = json.loads((OPT / "btc_cycle_20260915/FINAL_STRATEGIES.json").read_text())
    for p in profiles:
        rows.append(
            {
                "group": "最终循环优化版",
                "slot": p["slot"],
                "package": p["package"].replace(".", "/"),
                "engine": "Nautilus",
                **read_run(OPT / "btc_cycle_20260915/final_runs/full" / p["slot"]),
            }
        )
    rows.append(
        {
            "group": "BTC假跌破收复研究版",
            "slot": "48h_20%名义仓位",
            "package": "strategy_python/btc_failed_breakout_20260916",
            "engine": "custom_minute_OHLC",
            **read_run(OPT / "btc_entry_strategy_20260916/followup_runs/cap/0.2/full"),
        }
    )
    table = pd.DataFrame(rows)
    assert len(table) == 30 and table.package.nunique() == 30
    assert (
        table.start.str.startswith("2021-01-01").all()
        and table.end_exclusive.str.startswith("2026-08-31").all()
    )
    table.to_csv(ROOT / "main_versions.csv", index=False)
    annual = []
    for r in rows:
        d = pd.read_csv(
            PROJECT / r["run"] / "daily_equity.csv", index_col=0, parse_dates=True
        ).equity
        prior = 500
        for year, g in d.groupby(d.index.year):
            close = float(g.iloc[-1])
            annual.append(
                {
                    "group": r["group"],
                    "slot": r["slot"],
                    "year": int(year),
                    "net_pnl": close - prior,
                }
            )
            prior = close
        assert abs(prior - 500 - r["net_pnl"]) < 1e-4, r["run"]
    pd.DataFrame(annual).to_csv(ROOT / "main_annual.csv", index=False)
    evidence = json.loads((OPT / "btc_backtest_audit_20260916/run_evidence.json").read_text())
    all_runs = []
    for r in evidence:
        if not r["source_current"] or not r["artifacts_complete"]:
            continue
        all_runs.append(
            {
                "package": r["package"],
                "variant": r["variant"],
                "coverage": "full" if r["full"] else "partial",
                "engine": "Nautilus",
                **read_run(PROJECT / r["run"]),
            }
        )
    pd.DataFrame(all_runs).to_csv(ROOT / "all_current_native_runs.csv", index=False)
    custom = []
    study = OPT / "btc_entry_strategy_20260916"
    for file, stage in [("comparison.csv", "first"), ("followup_comparison.csv", "followup")]:
        frame = pd.read_csv(study / file)
        frame["round"] = stage
        frame["engine"] = "custom_minute_OHLC"
        frame["capital_model"] = frame.stage.map(
            {
                "exits": "fixed_notional_research",
                "time": "fixed_notional_research",
                "sizing": "cash_account",
                "cap": "cash_account",
                "cost": "cash_account",
            }
        )
        custom.append(frame)
    pd.concat(custom).to_csv(ROOT / "custom_simulations.csv", index=False)
    packages = pd.read_csv(OPT / "btc_backtest_audit_20260916/packages.csv").fillna("")
    packages.to_csv(ROOT / "all_51_packages_coverage.csv", index=False)
    lines = [
        "# BTC 各版本回测统计",
        "",
        "30个顶层版本逐项列出，另附21个内部代码目录覆盖情况、当前源码对应的全部原生回测、72项自建模拟。没有数据的目录不填零，不把分段结果冒充完整历史。",
        "",
        "主表区间2021-01-01至2026-08-30（UTC），每版本初始500 USDT；收益为累计而非年化，手续费已扣。盈亏比为平均盈利/平均亏损绝对值，不是利润因子。各版本独立账户，不能简单相加当组合回测。",
        "",
        "MRL=均值回归做多，TFL=趋势做多，TFS=趋势做空，PBS=回踩做空；original/optimized表示该批次所继承的位置。",
    ]
    for group, frame in table.groupby("group", sort=False):
        lines += ["", "## " + group, "", *markdown_table(frame)]
    lines += [
        "",
        "## 比较口径",
        "",
        "初始及尺度版PBS原/优化位的默认风险预算不同，不能把收益差异全部归因于信号；对应同预算对照保留在全部原生回测CSV。机制版表中采用main配置，不采用flat_risk或收益更好的实验替代。",
        "机制版5个本体通过移除2024起始限制的历史副本回放；最终循环版TFL/TFS/PBS各两个位置共享参数，结果相同并非六份独立证据。",
        "假跌破收复是自建分钟OHLC模拟，未作Nautilus原生验证；初始手续费模型为每边0.05%吃单。其余原生回测为挂单0.02%、吃单0.05%。不宜直接跨引擎按收益排名。资金费率、真实盘口与滑点未完整模拟；历史参与过选择，不能据此排除过拟合。",
        "",
        "## 内部实验包逐项覆盖",
        "",
        "| 目录 | 当前原生回测数 | 从2021开始的回测数 | 完整历史数 | 状态 |",
        "|---|---:|---:|---:|---|",
    ]
    for r in packages[~packages.top_level].itertuples():
        lines.append(
            f"| {r.package} | {r.current_native_runs} | {r.current_2021_start_runs} | {r.current_native_full_runs} | {r.status} |"
        )
    lines += [
        "",
        "## 全量数据文件",
        "",
        "- [30个主版本指标与回测来源](main_versions.csv)",
        "- [30个主版本逐年收益](main_annual.csv)",
        f"- [全部当前源码原生回测：{len(all_runs)}条，含各参数与分段](all_current_native_runs.csv)",
        "- [自建模拟72项：含固定名义研究与实际账户，已分栏](custom_simulations.csv)",
        "- [51个代码目录覆盖清单，含无数据目录](all_51_packages_coverage.csv)",
        "- [150个参数方案覆盖清单](../btc_backtest_audit_20260916/variants.csv)",
    ]
    (ROOT / "RESULTS.md").write_text("\n".join(lines) + "\n")
    print(
        table[
            [
                "group",
                "slot",
                "net_pnl",
                "return_pct",
                "max_drawdown_pct",
                "win_rate_pct",
                "payoff_ratio",
                "trades",
            ]
        ].to_string(index=False)
    )
    print("STATS_COMPLETE", len(table), "top-level;", len(all_runs), "native runs")


if __name__ == "__main__":
    main()
