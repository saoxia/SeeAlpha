"""Report all fixed BTC runs, including equal-risk PBS and source/ledger checks."""

import hashlib
import json
import os
from collections import Counter
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parents[2]
LABELS = {"MRL": "均值回归多头", "TFL": "趋势多头", "TFS": "趋势空头", "PBS": "小时反弹空头"}


def read_metrics(period, key):
    return json.loads((ROOT / "runs" / period / key / "metrics.json").read_text())


def main():
    strategies = json.loads((ROOT / "strategies.json").read_text())
    frozen = json.loads((ROOT / "frozen_btc_hashes.json").read_text())
    original = json.loads((ROOT / "source_hashes.json").read_text())
    for name, digest in {**original, **frozen}.items():
        assert hashlib.sha256((PROJECT / name).read_bytes()).hexdigest() == digest, name
    data = json.loads((ROOT / "data_audit.json").read_text())
    parity = json.loads((ROOT / "streaming_parity.json").read_text())
    assert len(parity) == 4 and all(p["identical"] and p["trades"] > 0 for p in parity.values())
    rows, order_audits = [], []
    for period in ("full", "recent"):
        for key in [*strategies, "PBS_original_matched"]:
            base_key = key.removesuffix("_matched")
            info = strategies[base_key]
            directory = ROOT / "runs" / period / key
            metric = read_metrics(period, key)
            parameters = json.loads((directory / "parameters.json").read_text())
            assert (
                metric["missing_minutes_including_edges"]
                == data["periods"][period]["missing_minutes"]
            )
            assert metric["off_tick_order_price_fields"] == 0
            assert abs(metric.get("cashflow_reconciliation_error", 0)) < 0.0001
            for name, digest in parameters["source_sha256"].items():
                assert digest == frozen[name], (key, name)
            reasons = Counter()
            for event, count in metric["order_event_reasons"].items():
                reasons[event.split(":", 1)[0]] += count
            order_audits.append({"period": period, "key": key, **dict(reasons)})
            rows.append(
                {
                    "period": period,
                    "key": key,
                    "family": info["family"],
                    "version": "matched" if key.endswith("_matched") else info["version"],
                    "net_pnl": metric["net_pnl"],
                    "return_pct": metric["return_pct"],
                    "max_drawdown_pct": metric["max_drawdown_pct"],
                    "trades": metric["cashflow_trades"],
                    "win_rate_pct": metric.get("cashflow_win_rate_pct", 0),
                    "payoff_ratio": metric["payoff_ratio"],
                    "profit_factor": metric.get("cashflow_profit_factor"),
                    "turnover_usdt": metric["turnover_usdt"],
                    "fees_usdt": metric["fees"],
                    "extra_2bp_pnl": metric["stress"]["2"]["net_pnl"],
                    "extra_5bp_pnl": metric["stress"]["5"]["net_pnl"],
                    "extra_10bp_pnl": metric["stress"]["10"]["net_pnl"],
                    "risk_fraction": parameters["config"].get("risk_fraction"),
                    "max_notional_fraction": parameters["config"].get("max_notional_fraction"),
                    "annual_pnl": metric["annual_marked_pnl"],
                }
            )
    assert len(rows) == 18
    matched_checks = {}
    for period in ("full", "recent"):
        left = read_metrics(period, "PBS_original_matched")
        right = read_metrics(period, "PBS_optimized")
        for field in ("net_pnl", "max_drawdown_pct", "turnover_usdt", "trades", "fees"):
            assert left[field] == right[field], (period, field)
        columns = ["side", "filled_qty", "avg_px", "commissions", "ts_last"]
        pd.testing.assert_frame_equal(
            pd.read_csv(ROOT / "runs" / period / "PBS_original_matched/fills.csv")[columns],
            pd.read_csv(ROOT / "runs" / period / "PBS_optimized/fills.csv")[columns],
        )
        matched_checks[period] = True
    (ROOT / "comparison.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n")
    pd.DataFrame(rows).drop(columns="annual_pnl").to_csv(ROOT / "comparison.csv", index=False)
    pd.DataFrame(order_audits).fillna(0).to_csv(ROOT / "order_audit.csv", index=False)

    os.environ.setdefault("MPLCONFIGDIR", str(PROJECT / ".local/cache/matplotlib"))
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(2, 2, figsize=(14, 8), layout="constrained")
    for ax, family in zip(axes.flat, LABELS):
        for version, color in (("original", "#c17835"), ("optimized", "#2366b1")):
            frame = pd.read_csv(
                ROOT / "runs/full" / f"{family}_{version}" / "daily_equity.csv",
                index_col=0,
                parse_dates=True,
            )
            label = version.title()
            if family == "PBS":
                label += " (risk 0.5%)" if version == "original" else " (risk 1.5%)"
            ax.plot(frame.index, (frame.equity / 500 - 1) * 100, label=label, color=color, lw=1.2)
        ax.set_title(family)
        ax.set_ylabel("Return (%)")
        ax.axhline(0, color="#888", lw=0.6)
        ax.grid(alpha=0.15)
        ax.legend(frameon=False)
    fig.suptitle("BTC-USDT | fixed ETH parameters | 500 USDT per strategy | through 2026-08-30")
    fig.savefig(ROOT / "equity_comparison.png", dpi=160)
    plt.close(fig)

    lines = [
        "# BTC-USDT八策略迁移回测结果",
        "",
        "八个独立BTC策略已建立，保留对应ETH原版/优化版的默认交易逻辑和参数；本轮未用BTC数据重新选参。",
        "[策略目录与来源](strategies.json) · [详细CSV](comparison.csv) · [执行协议](PROTOCOL.md)",
        "",
        "每个独立账户初始500 USDT，保证金杠杆5倍，1分钟原生Nautilus回测。收益已扣maker 2bp/taker 5bp模拟手续费，未含历史资金费率和真实滑点。",
        "",
    ]
    for period, label in (("full", "全区间"), ("recent", "近期区间")):
        positive = [
            r
            for r in rows
            if r["period"] == period and r["version"] != "matched" and r["net_pnl"] > 0
        ]
        names = (
            "、".join(
                LABELS[r["family"]] + ("原版" if r["version"] == "original" else "优化版")
                for r in positive
            )
            or "无"
        )
        lines.append(f"**{label}：8个默认版本中{len(positive)}个净盈利，盈利版本为{names}。**")
    lines += [
        "",
        "这是固定ETH参数在BTC上的实际表现；需要将收益、回撤、近期稳定性和成本一起看，不能仅凭某个版本相对少亏就称迁移成功。",
        "",
    ]
    for period, title in (
        ("full", "全区间：2021-01-01至2026-08-30"),
        ("recent", "近期独立复核：2025-01-01至2026-08-30"),
    ):
        lines += [
            "## " + title,
            "",
            "| 策略 | 版本 | 净收益USDT | 收益率 | 最大回撤 | 交易数 | 胜率 | 盈亏比 |",
            "|---|---|---:|---:|---:|---:|---:|---:|",
        ]
        for row in rows:
            if row["period"] != period or row["version"] == "matched":
                continue
            ratio = f"{row['payoff_ratio']:.3f}" if row["payoff_ratio"] is not None else "—"
            lines.append(
                f"| {LABELS[row['family']]} | {'原版' if row['version'] == 'original' else '优化版'} | "
                f"{row['net_pnl']:.2f} | {row['return_pct']:.2f}% | {row['max_drawdown_pct']:.2f}% | "
                f"{row['trades']} | {row['win_rate_pct']:.2f}% | {ratio} |"
            )
        lines += [
            "",
            "近期复核从现金500重新开始，不能与其他时段收益直接相加。"
            if period == "recent"
            else "盈亏比=平均盈利交易/平均亏损绝对值；交易按完整持仓编号聚合成交现金流。",
            "",
        ]
    lines += ["## 如何理解迁移结果", ""]
    for family in ("MRL", "TFL", "TFS"):
        outcomes = []
        for period in ("full", "recent"):
            old = next(
                r for r in rows if r["period"] == period and r["key"] == family + "_original"
            )
            new = next(
                r for r in rows if r["period"] == period and r["key"] == family + "_optimized"
            )
            outcomes.append(
                f"{'全期' if period == 'full' else '近期'}优化版净收益比原版"
                f"{'增加' if new['net_pnl'] >= old['net_pnl'] else '减少'}"
                f"{abs(new['net_pnl'] - old['net_pnl']):.2f} USDT"
            )
        lines.append(
            f"- {LABELS[family]}："
            + "，".join(outcomes)
            + "。是否盈利及回撤变化需同时看上表，收益改善不等于排除过拟合。"
        )
    lines += [
        "",
        "## PBS同风险预算核对",
        "",
        "默认原版单笔风险0.5%、名义上限1倍；优化版1.5%、3倍。这是源策略默认值的差异，不是BTC上新发现的优势。",
        "",
        "| 区间 | 原版覆盖1.5%/3倍净收益 | 优化版默认净收益 | 成交时间/数量/价格/手续费 |",
        "|---|---:|---:|---|",
    ]
    for period in ("full", "recent"):
        m = read_metrics(period, "PBS_original_matched")
        n = read_metrics(period, "PBS_optimized")
        lines.append(f"| {period} | {m['net_pnl']:.2f} | {n['net_pnl']:.2f} | 完全一致 |")
    lines += [
        "",
        "**PBS原/新版在同预算下是同一交易规则，默认收益差异来自预算、数量取整与复利，不能当作择时优化。**",
        (
            f"全期原默认{read_metrics('full', 'PBS_original')['trades']}笔，"
            f"优化版默认{read_metrics('full', 'PBS_optimized')['trades']}笔；"
            "同预算后成交笔数也完全一致。因此差异不只是每笔盈亏放大，"
            "还涉及小账户预算与数量取整后的交易机会。"
        ),
        "",
        "## 全区间权益",
        "",
        "![BTC独立策略权益](equity_comparison.png)",
        "",
        "## 全区间额外成交成本压力",
        "",
        "固定原成交路径，每次成交额外扣费；不会重算订单或数量，不是滑点重跑。",
        "",
        "| 策略/版本 | 成交额USDT | 已扣手续费USDT | 再加2bp净收益 | 再加5bp净收益 | 再加10bp净收益 |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        if row["period"] == "full" and row["version"] != "matched":
            lines.append(
                f"| {row['key']} | {row['turnover_usdt']:.2f} | {row['fees_usdt']:.2f} | "
                f"{row['extra_2bp_pnl']:.2f} | {row['extra_5bp_pnl']:.2f} | {row['extra_10bp_pnl']:.2f} |"
            )
    lines += [
        "",
        "## 全区间逐年权益变化",
        "",
        "同一次复利运行按年末分钟权益计算；不等于每年独立500账户，2026仅至8月30日。",
        "",
        "| 策略/版本 | 2021 | 2022 | 2023 | 2024 | 2025 | 2026部分 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        if row["period"] == "full" and row["version"] != "matched":
            lines.append(
                "| "
                + row["key"]
                + " | "
                + " | ".join(f"{row['annual_pnl'].get(str(y), 0):.2f}" for y in range(2021, 2027))
                + " |"
            )
    lines += [
        "",
        "## 数据、执行及结论边界",
        "",
        (
            f"- 原始BTC分钟覆盖2020-01-01至2026-08-30，无重复；全部覆盖内缺{data['total_missing_inside_coverage']}分钟，"
            f"本次全区间缺{data['periods']['full']['missing_minutes']}分钟，近期缺{data['periods']['recent']['missing_minutes']}分钟。缺口未补造。"
        ),
        "- 此次比旧ETH报告少2026年8月31日，不能做严格同区间资产收益差比较。",
        "- 90天分批加载，策略/账户连续；四个家族已在有实际交易的2022年5月核对一次性/分批成交及权益一致。",
        "- 回撤包含未实现盈亏，以分钟收盘计算，不是分钟内最坏权益；未做历史资金费率、历史保证金阶梯或真实盘口重放。",
        "- 合约定义取当前Catalog快照，价差步长0.10 USDT、数量步长0.001 BTC、最低名义金额50 USDT。原生模拟器不等于交易所所有历史订单规则的完整仿真。",
        "- 所有最终订单价格字段均符合BTC价差步长。撤单/改单失败与建仓拒绝分开记录在[订单事件审计](order_audit.csv)，原始原因保存在各metrics.json。",
        "- 500 USDT小账户受数量步长/最小订单限制，不能把取整后交易机会减少误认作过滤条件更有效。",
        "- 本轮BTC未参与调参，但不证明项目此前未使用过BTC历史；同一时期加密资产相关，跨资产结果不能替代未来时间验证。",
        "- 各策略为独立账户，不是共享账户组合；不能相加声称组合收益。",
        "",
        "## 复现与检查",
        "",
        "22项迁移/架构检查通过；所有领域代码在包名、类名及合约标识替换后与来源一致；原ETH源码哈希保持不变。",
        "每次运行保存实际类默认配置、合约参数、源文件哈希、成交、持仓、账户、日权益及metrics.json。18次正式结果全部与成交现金流对账。",
        "",
        "批量运行：`.venv/bin/python -m strategy_python._optimization.btc_transfer_20260914.evaluate`。",
        "生成报告：`.venv/bin/python -m strategy_python._optimization.btc_transfer_20260914.report`。",
        "单策略入口见各BTC目录README；批量运行用分批加载以控制全历史内存。",
        "",
    ]
    (ROOT / "RESULTS.md").write_text("\n".join(lines))
    (ROOT / "verification.json").write_text(
        json.dumps(
            {
                "completed_formal_runs": len(rows),
                "original_source_files_unchanged": len(original),
                "frozen_btc_source_files_verified": len(frozen),
                "streaming_parity": parity,
                "matched_pbs": matched_checks,
                "all_cashflows_reconcile": True,
                "all_final_order_price_fields_on_tick": True,
            },
            indent=2,
        )
        + "\n"
    )
    print("REPORT_COMPLETE", flush=True)


if __name__ == "__main__":
    main()
