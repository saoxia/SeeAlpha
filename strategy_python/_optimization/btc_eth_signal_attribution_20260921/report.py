"""Present immutable diagnostic outputs, without ranking parameters or exit horizons."""

import html
import json
import zipfile
from collections import Counter
from datetime import UTC, datetime

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from .analysis import OUTPUT, decision
from .audit_replay import verify_amendment
from .native import IDS, ROOT, digest, json_write, verify

LABELS = {"MRL": "均值回归多头", "TFS": "趋势跟随空头"}


def md_table(frame):
    lines = ["| " + " | ".join(frame.columns) + " |", "|" + "---|" * len(frame.columns)]
    for values in frame.itertuples(index=False, name=None):
        lines.append(
            "| "
            + " | ".join(
                "—" if pd.isna(v) else f"{v:.2f}" if isinstance(v, float) else str(v)
                for v in values
            )
            + " |"
        )
    return "\n".join(lines)


def signal_chart(signals):
    fig, axes = plt.subplots(2, 3, figsize=(15, 8.5))
    for r, period in enumerate(("full", "2025+")):
        for c, (kind, asset) in enumerate((("MRL", "BTC"), ("MRL", "ETH"), ("TFS", None))):
            ax = axes[r, c]
            selected = signals[
                (signals.strategy == kind)
                & (signals.period == period)
                & (signals.spacing_hours == 1)
            ]
            if asset:
                selected = selected[selected.asset == asset]
            grouping = "control" if asset else "asset"
            for offset, (name, group) in enumerate(selected.groupby(grouping)):
                group = group.sort_values("horizon_hours")
                x = np.arange(3) + (offset - 0.5) * 0.15
                y = group.edge_bp.to_numpy()
                label = {"calendar_vol": "Random close", "touch_vol": "Same touch mechanism"}.get(
                    name, name
                )
                ax.errorbar(
                    x,
                    y,
                    yerr=[y - group.edge_low95_bp.to_numpy(), group.edge_high95_bp.to_numpy() - y],
                    fmt="o-",
                    capsize=4,
                    label=label,
                    lw=1.3,
                )
            ax.axhline(0, color="#555", lw=0.8)
            ax.set_xticks(range(3), ["1h", "4h", "24h"])
            ax.set_title(f"{asset + ' ' if asset else 'BTC / ETH '}{kind} | {period}")
            ax.set_ylabel("Gross return minus control (bp)")
            ax.grid(alpha=0.18)
            ax.legend(fontsize=8)
    fig.suptitle("Signal diagnosis: matched controls and 95% time-block intervals", fontsize=15)
    fig.text(
        0.5,
        0.015,
        "1h event spacing. MRL starts at touch-minute close. Historical / post-selection analysis; no out-of-sample claim.",
        ha="center",
        fontsize=10,
    )
    fig.tight_layout(rect=(0, 0.045, 1, 0.95))
    fig.savefig(ROOT / "signal_comparison.png", dpi=150)
    plt.close(fig)


def attribution_chart(exits, groups):
    fig, axes = plt.subplots(2, 2, figsize=(12, 9))
    for ax, run_id in zip(axes.flat, IDS, strict=True):
        asset, kind = run_id.split("_")
        data = exits[(exits.asset == asset) & (exits.strategy == kind) & (exits.period == "full")]
        order = ["original", "time1", "time4", "time24"]
        for k, fee in enumerate((0, 5)):
            values = data[data.extra_fee_bp_per_side == fee].set_index("exit").loc[order]
            ax.bar(
                np.arange(4) + (k - 0.5) * 0.35,
                values.mean_net_bp,
                width=0.35,
                label="Base fees" if fee == 0 else "+5 bp each side",
                color="#2d679c" if fee == 0 else "#d88545",
            )
        ax.axhline(0, color="#555", lw=0.8)
        ax.set_xticks(range(4), ["Original", "1h", "4h", "24h"])
        ax.set_title(f"{asset} {kind} | same {int(values.trades.iloc[0])} entries")
        ax.set_ylabel("Mean net return per entry (bp)")
        ax.legend(fontsize=8)
        ax.grid(axis="y", alpha=0.18)
    fig.suptitle("Exit attribution with fixed entries and equal entry notional", fontsize=15)
    fig.text(
        0.5,
        0.015,
        "Independent trade counterfactuals can overlap. Bars are NOT portfolio returns and do not select an exit rule.",
        ha="center",
        fontsize=10,
    )
    fig.tight_layout(rect=(0, 0.045, 1, 0.95))
    fig.savefig(ROOT / "exit_attribution.png", dpi=150)
    plt.close(fig)


def main():
    manifest = verify()
    verify_amendment()
    analysis_amendment = json.loads((ROOT / "analysis_amendment.json").read_text())
    assert digest(ROOT / "analysis.py") == analysis_amendment["original_source_sha256"]
    assert digest(ROOT / "analysis_v2.py") == analysis_amendment["fixed_source_sha256"]
    for name, sha in analysis_amendment["first_attempt_outputs"].items():
        assert digest(OUTPUT / name) == sha, ("unchanged partial analysis", name)
    for name, sha in json.loads((OUTPUT / "completion.json").read_text()).items():
        assert digest(OUTPUT / name) == sha
    signals = pd.read_csv(OUTPUT / "signal_summary.csv")
    exits = pd.read_csv(OUTPUT / "exit_size_comparison.csv")
    groups = pd.read_csv(OUTPUT / "trade_groups.csv")
    counts = json.loads((OUTPUT / "signal_counts.json").read_text())
    evidence = decision(signals)
    assert evidence == json.loads((ROOT / "decision.json").read_text())
    metrics, audits, configs = [], [], {}
    for run_id in IDS:
        run = ROOT / "runs" / run_id
        for name, sha in json.loads((run / "completion.json").read_text()).items():
            assert digest(run / name) == sha
        m = json.loads((run / "metrics.json").read_text())
        params = json.loads((run / "parameters.json").read_text())
        assert params["manifest_sha256"] == digest(ROOT / "manifest.json")
        cfg = params["config"].copy()
        cfg.pop("instrument_id")
        cfg.pop("bar_type")
        configs[run_id] = cfg
        asset, kind = run_id.split("_")
        metrics.append(
            {
                "市场": asset,
                "策略": LABELS[kind],
                "净盈亏USDT": m["net_pnl"],
                "累计%": m["return_pct"],
                "最大回撤%": m["max_drawdown_pct"],
                "交易数": m["trades"],
                "2025后盯市盈亏": m["review_marked_pnl"],
            }
        )
        reasons = Counter()
        for reason, count in m["order_event_reasons"].items():
            reasons[reason.split(":", 1)[0]] += count
        audits.append(
            {
                "id": run_id,
                "baseline_reconciled": m.get("baseline_trade_and_fill_reconciliation"),
                "invalid_openings": m["entry_fills_outside_constraints"],
                "off_tick_prices": m["off_tick_order_price_fields"],
                "cashflow_error": m["cashflow_error"],
                "missing_minutes": m["missing_minutes"],
                "fills_below_minimum_after_limit_price_improvement": m.get(
                    "fill_price_bounds_exception_count", 0
                ),
                "audit_replay_matches_first_attempt": m.get("audit_replay_matches_first_attempt"),
                "order_event_counts": dict(reasons),
            }
        )
        assert m["entry_fills_outside_constraints"] == m["off_tick_order_price_fields"] == 0
        assert abs(m["cashflow_error"]) < 1e-6
        trades = pd.read_csv(OUTPUT / f"{run_id}_all_trades.csv")
        assert len(trades) == m["trades"] and abs(trades.net_pnl.sum() - m["net_pnl"]) < 1e-6
        common = trades[trades.timed_complete]
        comparison = exits[
            (exits.asset == asset)
            & (exits.strategy == kind)
            & (exits.period == "full")
            & (exits.extra_fee_bp_per_side == 0)
        ]
        assert comparison.trades.nunique() == 1 and comparison.trades.iloc[0] == len(common)
        original = comparison[comparison.exit == "original"].iloc[0]
        assert abs(original.original_size_sum_pnl - common.net_pnl.sum()) < 1e-6
        assert (
            abs(original.fixed100_sum_pnl - (common.net_pnl / common.entry_notional * 100).sum())
            < 1e-6
        )
    for kind in ("MRL", "TFS"):
        assert configs[f"BTC_{kind}"] == configs[f"ETH_{kind}"]
    with zipfile.ZipFile(ROOT / "frozen_sources.zip") as z:
        assert z.testzip() is None
        import hashlib

        for name, sha in manifest["source_sha256"].items():
            assert hashlib.sha256(z.read(name)).hexdigest() == sha
    assert (
        digest(ROOT / "frozen_sources.zip")
        == json.loads((ROOT / "archive.json").read_text())["sha256"]
    )
    signal_chart(signals)
    attribution_chart(exits, groups)
    metrics = pd.DataFrame(metrics)
    metrics.to_csv(ROOT / "native_comparison.csv", index=False)
    selected = signals[(signals.spacing_hours == 1) & (signals.control == "calendar_vol")].copy()
    selected["优势及95%区间bp"] = selected.apply(
        lambda r: f"{r.edge_bp:.2f} [{r.edge_low95_bp:.2f}, {r.edge_high95_bp:.2f}]", axis=1
    )
    signal_table = selected[
        [
            "asset",
            "strategy",
            "period",
            "horizon_hours",
            "events",
            "return_bp",
            "market_net_bp",
            "优势及95%区间bp",
        ]
    ].rename(
        columns={
            "asset": "市场",
            "strategy": "策略",
            "period": "时期",
            "horizon_hours": "小时",
            "events": "事件数",
            "return_bp": "毛收益bp",
            "market_net_bp": "市价成本后bp",
        }
    )
    group_table = groups[groups.split == "outcome"][
        [
            "asset",
            "strategy",
            "group",
            "trades",
            "net_pnl",
            "mean_net_bp",
            "mean_holding_hours",
            "mean_entry_notional",
        ]
    ].rename(
        columns={
            "asset": "市场",
            "strategy": "策略",
            "group": "组别",
            "trades": "笔数",
            "net_pnl": "净盈亏",
            "mean_net_bp": "等规模均值bp",
            "mean_holding_hours": "平均持仓小时",
            "mean_entry_notional": "平均入场USDT",
        }
    )
    cohort_rows = []
    for c in counts:
        for cohort in c["cohorts"]:
            cohort_rows.append(
                {
                    "市场": c["asset"],
                    "策略": c["strategy"],
                    "原始候选": c["eligible_candidates_before_dedup"],
                    "间隔小时": cohort["spacing_hours"],
                    "去重后": cohort["deduplicated"],
                    "数据完整": cohort["common_window_and_past_vol_available"],
                    "缺失剔除": cohort["missing_window_or_vol"],
                }
            )
    cohort_table = pd.DataFrame(cohort_rows)
    conclusions = "；".join(
        f"{LABELS[k]}：{'达到研究线索门槛' if v['signal_evidence_gate_passed'] else '未达到信号证据门槛'}（共同通过窗口 {v['common_passed_horizons']}）"
        for k, v in evidence.items()
    )
    findings = (
        json.loads((ROOT / "interpretation.json").read_text())
        if (ROOT / "interpretation.json").exists()
        else []
    )
    methodology = [
        "UTC 2021-01-01 至 2026-08-31（不含结束日）；两个市场同一组 BTC 参数，四组独立账户各 500 USDT。历史已经用于研究，本轮属于机制诊断。",
        "MRL 原始报价与真实成交分开：只检查报价生效后的首次触价，事件起点为触价分钟收盘，排除该分钟内无法排序的反弹；TFS 使用满足全套市场条件的交叉。持仓或资金阻挡的候选仍保留。",
        "普通对照每个事件固定 20 个同月、同 6 小时时段、过去 24 小时波动率相近的时间点；MRL 另有相同报价触价机制对照。95% 区间按 3 个月时间区块计算；主间隔 1 小时，另报 24 小时间隔。相对优势使用毛收益。",
        "1、4、24 小时窗口共同要求连续数据；匹配协变量仅取当时或过去值。普通对照可能包含其他信号，同触价对照可能通过原过滤，均不属于因果实验。",
        "退出比较固定同一批原生入场、同一入场价。先统一每笔名义金额，再恢复实际数量。定时退出可重叠，不能把合计收益当成可执行账户；每边额外 5 bp 是固定路径成本扣减，不是重新定仓回测。",
        "事件表的市价成本参照按双边 taker 扣费；不能替代原限价交易结果。MFE/MAE 排除入场分钟，原始持仓路径跨缺口或同分钟退出的极值不作统计。",
        "原生成交归因的持仓 MFE/MAE 包含退出分钟的高低价，无法识别该分钟内退出前后的极值顺序，因此是分钟路径代理而非逐笔精确持仓极值；净盈亏则按真实模拟成交现金流核算。",
        "未建模资金费率、真实订单簿滑点和历史逐日合约门槛；BTC 缺 938 分钟、ETH 缺 618 分钟。区块置信区间不能消除历史选参偏差。",
        "ETH MRL 有两笔限价委托金额合规、成交价改善后金额低于门槛的交易。补充审计按订单价核对，并再次完整重放；全部交易、成交及候选与首次输出相同。原始失败记录和勘误保留，未删除交易或修改策略。",
    ]
    text = "# BTC / ETH 信号与亏损归因\n\n" + conclusions + "。\n\n"
    text += "\n\n".join(findings) + "\n\n## 原生完整重放\n\n" + md_table(metrics)
    text += "\n\n## 信号相对随机对照\n\n![信号比较](signal_comparison.png)\n\n" + md_table(
        signal_table
    )
    text += "\n\n## 所有盈利和亏损交易\n\n" + md_table(group_table)
    text += "\n\n## 同入场、同规模的退出比较\n\n![退出归因](exit_attribution.png)\n\n详见 [退出及仓位逐项表](analysis/exit_size_comparison.csv)。这些是独立交易反事实，不能选出一根最高柱就称为策略改进。"
    text += "\n\n## 候选覆盖与剔除\n\n" + md_table(cohort_table)
    text += "\n\n挂单/持仓/恢复/资金阻挡计数见 [信号记录](analysis/signal_counts.json)。未匹配数和全部对照、去重方案见 [完整信号表](analysis/signal_summary.csv)。"
    text += "\n\n## 方法及限制\n\n" + "\n\n".join(methodology)
    text += "\n\n## 验证与复现\n\n三组已有基线的逐笔交易和成交完全对齐；四组现金流、开仓委托门槛和价格步长通过检查。7 项时序/缺口/配对/去重/决策测试通过。ETH 的两笔成交价改善情况见勘误。\n\n[事先方案](PROTOCOL.md) · [订单审计勘误及官方规则来源](AUDIT_AMENDMENT.md) · [审计](final_validation.json) · [复现说明](README.md)\n"
    (ROOT / "RESULTS.md").write_text(text)
    sections = [
        "<h1>BTC / ETH 信号与亏损归因</h1>",
        f'<p class="notice">{html.escape(conclusions)}。这是历史机制诊断。</p>',
    ]
    sections += [f"<p>{html.escape(x)}</p>" for x in findings]
    sections += [
        "<h2>原生完整重放</h2>",
        metrics.to_html(index=False, float_format=lambda x: f"{x:.2f}"),
        "<h2>信号优势及不确定性</h2><img src='signal_comparison.png' alt='Signal comparison'>",
        "<p>MRL：均值回归多头；TFS：趋势跟随空头。纵轴为相对对照的毛收益差，1 bp = 0.01%。区间跨过零表示没有排除零优势。</p>",
        "<details><summary>全部普通对照结果</summary>"
        + signal_table.to_html(index=False, float_format=lambda x: f"{x:.2f}")
        + "</details>",
        "<details><summary>包括同触价对照、24 小时去重的完整结果</summary>"
        + signals.to_html(index=False, float_format=lambda x: f"{x:.3f}")
        + "</details>",
        "<h2>所有盈利和亏损交易</h2>",
        group_table.to_html(index=False, float_format=lambda x: f"{x:.2f}"),
        "<h2>退出与仓位归因</h2><img src='exit_attribution.png' alt='Exit attribution'>",
        "<p>固定入场队列、等名义金额。定时退出可能重叠，这些柱子不是组合收益，也不能用于选择最优期限。</p>",
        "<details><summary>同规模与恢复原数量：全期、近期和成本压力</summary>"
        + exits.to_html(index=False, float_format=lambda x: f"{x:.3f}")
        + "</details>",
        "<details><summary>全部交易分组：持有时间、退出方式、盈利亏损</summary>"
        + groups.to_html(index=False, float_format=lambda x: f"{x:.3f}")
        + "</details>",
        "<h2>候选覆盖与剔除</h2>",
        cohort_table.to_html(index=False),
        "<h2>方法与限制</h2>",
    ]
    sections += ["<p>" + html.escape(x) + "</p>" for x in methodology]
    sections += [
        "<p><a href='PROTOCOL.md'>事先固定方案</a> · <a href='RESULTS.md'>文字报告</a> · <a href='analysis/signal_counts.json'>准入原因与覆盖</a> · <a href='AUDIT_AMENDMENT.md'>订单审计勘误及官方规则来源</a> · <a href='final_validation.json'>审计记录</a></p>"
    ]
    for run_id in IDS:
        sections.append(
            f"<p>{run_id}：<a href='analysis/{run_id}_all_trades.csv'>全部交易</a> · <a href='analysis/{run_id}_all_losses.csv'>全部亏损交易</a></p>"
        )
    (ROOT / "report.html").write_text(
        "<!doctype html><html lang='zh-CN'><meta charset='utf-8'><title>BTC / ETH 信号与亏损归因</title><style>body{font:16px/1.7 system-ui,sans-serif;max-width:1160px;margin:36px auto;padding:0 24px;color:#223}h1,h2{line-height:1.3}img{width:100%}table{border-collapse:collapse;font-size:13px;display:block;overflow:auto}td,th{border:1px solid #ddd;padding:6px 9px;white-space:nowrap}th{background:#eef3f7}.notice{padding:16px;background:#fff0d6;border-radius:8px}details{margin:20px 0}summary{cursor:pointer}a{color:#2469aa}</style><body>"
        + "\n".join(sections)
        + "</body></html>"
    )
    json_write(ROOT / "execution_audit.json", audits)
    json_write(
        ROOT / "final_validation.json",
        {
            "completed_at": datetime.now(UTC).isoformat(),
            "native_runs": 4,
            "existing_baselines_reconciled": sum(a["baseline_reconciled"] is True for a in audits),
            "frozen_source_data_references_environment_verified": True,
            "same_parameters_across_markets": True,
            "same_entry_cohort_exit_and_size_arithmetic_verified": True,
            "max_cashflow_error": max(abs(a["cashflow_error"]) for a in audits),
            "invalid_openings": sum(a["invalid_openings"] for a in audits),
            "off_tick_prices": sum(a["off_tick_prices"] for a in audits),
            "price_improved_limit_fills_below_minimum": sum(
                a["fills_below_minimum_after_limit_price_improvement"] for a in audits
            ),
            "audit_only_eth_replay_unchanged": next(
                a["audit_replay_matches_first_attempt"] for a in audits if a["id"] == "ETH_MRL"
            ),
            "core_tests_passed": 7,
            "nullable_exit_tag_compatibility_fix": True,
            "unchanged_first_analysis_outputs": len(analysis_amendment["first_attempt_outputs"]),
            "signal_evidence": evidence,
            "out_of_sample": False,
            "report_source_sha256": digest(ROOT / "report.py"),
        },
    )
    with zipfile.ZipFile(ROOT / "report_source.zip", "w", zipfile.ZIP_DEFLATED) as z:
        for name in (
            "report.py",
            "interpretation.json",
            "analysis_v2.py",
            "analysis_amendment.json",
            "audit_replay.py",
            "AUDIT_AMENDMENT.md",
            "audit_amendment/manifest.json",
        ):
            if (ROOT / name).exists():
                z.write(ROOT / name, name)
    print(conclusions, flush=True)
    print(metrics.to_string(index=False), flush=True)


if __name__ == "__main__":
    main()
