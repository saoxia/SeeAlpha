"""Report fixed long-strategy replays and distinguish the old execution model."""

import html
import json
import zipfile
from collections import Counter
from datetime import UTC, datetime

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

from .run import HISTORY, KEYS, ROOT, digest, json_write, verify

LABELS = {"trend_following_long": "趋势跟随多头", "mean_reversion_long": "均值回归多头"}
ENGLISH = {
    "trend_following_long": "Trend following long",
    "mean_reversion_long": "Mean reversion long",
}


def table(frame, columns, labels):
    lines = ["| " + " | ".join(labels) + " |", "|" + "---|" * len(columns)]
    for _, row in frame.iterrows():
        values = []
        for column in columns:
            v = row[column]
            values.append("—" if pd.isna(v) else f"{v:.2f}" if isinstance(v, float) else str(v))
        lines.append("| " + " | ".join(values) + " |")
    return "\n".join(lines)


def read_csv(path):
    try:
        return pd.read_csv(path)
    except pd.errors.EmptyDataError:
        return pd.DataFrame()


def main():
    manifest = json.loads((ROOT / "manifest.json").read_text())
    verify(manifest)
    progress = json.loads((ROOT / "progress.json").read_text())
    expected_ids = {r["id"] for r in manifest["experiments"]}
    assert set(progress["completed"]) == expected_ids and not progress["failed"]
    current, audits, annual = [], [], []
    for row in manifest["experiments"]:
        output = ROOT / "runs" / row["id"]
        for name, expected in json.loads((output / "completion.json").read_text()).items():
            assert digest(output / name) == expected, (row["id"], name)
        parameters = json.loads((output / "parameters.json").read_text())
        assert parameters["experiment"] == row
        assert parameters["manifest_sha256"] == digest(ROOT / "manifest.json")
        stats = json.loads((output / "metrics.json").read_text())
        assert stats["entry_fills_outside_constraints"] == stats["off_tick_order_price_fields"] == 0
        assert abs(stats["cashflow_error"]) < 1e-6
        trades = read_csv(output / "trades.csv")
        max_hold = None
        if len(trades):
            max_hold = (
                (
                    pd.to_datetime(trades.exit_time, utc=True)
                    - pd.to_datetime(trades.entry_time, utc=True)
                ).dt.total_seconds()
                / 86400
            ).max()
        record = {
            **row,
            **{k: v for k, v in stats.items() if not isinstance(v, dict)},
            "label": LABELS[row["strategy"]],
            "max_holding_days": max_hold,
        }
        current.append(record)
        yearly = pd.read_csv(output / "annual.csv")
        for a in yearly.to_dict("records"):
            annual.append(
                {
                    "strategy": row["strategy"],
                    "label": LABELS[row["strategy"]],
                    "extra_fee_bp": row["extra_fee_bp"],
                    **a,
                }
            )
        events = Counter()
        for reason, count in stats["order_event_reasons"].items():
            events[reason.split(":", 1)[0]] += count
        audits.append(
            {
                "id": row["id"],
                "invalid_openings": stats["entry_fills_outside_constraints"],
                "off_tick_prices": stats["off_tick_order_price_fields"],
                "cashflow_error": stats["cashflow_error"],
                "missing_minutes": stats["missing_minutes"],
                "order_events": dict(events),
                "first_fill": stats["first_fill"],
                "last_fill": stats["last_fill"],
            }
        )
    frame = pd.DataFrame(current)
    frame.to_csv(ROOT / "comparison.csv", index=False)
    yearly = pd.DataFrame(annual)
    yearly.to_csv(ROOT / "annual_comparison.csv", index=False)
    old_rows = []
    for key, slot in KEYS.items():
        directory = HISTORY / slot
        old = json.loads((directory / "metrics.json").read_text())
        fills = read_csv(directory / "fills.csv")
        entries = fills[~fills.is_reduce_only.astype(bool)] if len(fills) else fills
        invalid = (
            int((entries.filled_qty.astype(float) * entries.avg_px.astype(float) < 50 - 1e-8).sum())
            if len(entries)
            else 0
        )
        new = frame[(frame.strategy == key) & (frame.extra_fee_bp == 0)].iloc[0]
        old_rows.append(
            {
                "strategy": key,
                "label": LABELS[key],
                "old_return_pct": old["return_pct"],
                "new_return_pct": new.return_pct,
                "old_drawdown_pct": old["max_drawdown_pct"],
                "new_drawdown_pct": new.max_drawdown_pct,
                "old_trades": old["trades"],
                "new_trades": new.trades,
                "old_invalid_openings": invalid,
                "new_invalid_openings": new.entry_fills_outside_constraints,
                "net_pnl_difference": new.net_pnl - old["net_pnl"],
            }
        )
    old_frame = pd.DataFrame(old_rows)
    old_frame.to_csv(ROOT / "before_after.csv", index=False)
    decision = {}
    for key in KEYS:
        rows = frame[frame.strategy == key]
        base = rows[rows.extra_fee_bp == 0].iloc[0]
        tests = {
            "full_profit_at_both_costs": bool((rows.net_pnl > 0).all()),
            "recent_profit_at_both_costs": bool((rows.review_marked_pnl > 0).all()),
            "minimum_trade_counts": bool(base.trades >= 50 and base.review_closed_trades >= 20),
            "valid_execution": bool((rows.entry_fills_outside_constraints == 0).all()),
        }
        decision[key] = {"passed": all(tests.values()), "checks": tests}
    json_write(ROOT / "decision.json", decision)
    json_write(ROOT / "execution_audit.json", audits)

    fig, axes = plt.subplots(2, 2, figsize=(12, 8), layout="constrained", sharex=True)
    for col, (key, slot) in enumerate(KEYS.items()):
        directories = [(HISTORY / slot, "Before constraint fix", "#777777", "--")]
        directories += [
            (ROOT / "runs" / (key + f"_fee{fee}"), label, color, "-")
            for fee, label, color in (
                (0, "Corrected / base fees", "#1766a3"),
                (5, "Corrected / +5 bp per fill", "#d06420"),
            )
        ]
        for directory, label, color, style in directories:
            daily = pd.read_csv(directory / "daily_equity.csv", index_col=0, parse_dates=True)
            axes[0, col].plot(
                daily.index, daily.equity, label=label, color=color, linestyle=style, linewidth=1.2
            )
            axes[1, col].plot(
                daily.index, -daily.drawdown_pct, color=color, linestyle=style, linewidth=1.2
            )
        axes[0, col].set_title(ENGLISH[key])
        axes[0, col].axhline(500, color="#999999", linewidth=0.6)
        axes[0, col].legend(fontsize=8)
        axes[0, col].set_ylabel("Equity (USDT)")
        axes[1, col].set_ylabel("Drawdown (%)")
        for row in (0, 1):
            axes[row, col].grid(alpha=0.2)
    fig.suptitle(
        "BTC fixed long strategies | 2021-01-01 to 2026-08-30\nDaily close curves; reported maximum drawdown uses minute closes",
        fontsize=13,
    )
    fig.savefig(ROOT / "equity_comparison.png", dpi=160)
    plt.close(fig)

    passed = [LABELS[k] for k, v in decision.items() if v["passed"]]
    conclusion = (
        "两项多头均未通过预先声明的基础有效性筛查，保留失败结果，不扩大这两个版本的参数搜索。"
        if not passed
        else "通过本轮基础筛查：" + "、".join(passed) + "。仍需独立未来时间和参数邻域验证。"
    )
    reasons_total = Counter()
    for audit in audits:
        reasons_total.update(audit["order_events"])
    base = frame[frame.extra_fee_bp == 0]
    text = f"""# BTC两个多头完整历史复核

{conclusion}

4/4次原生回放完成。UTC 2021-01-01至2026-08-30，各自独立500 USDT账户、5倍保证金杠杆。
与旧报告逐项核对后，配置、预热、手续费、数量/价格步长、最低金额快照和引擎版本均一致。
本轮保持信号、参数和风险预算，使用已修复的开仓约束。额外5bp为每次成交增加0.05%手续费并完整重跑。
下表收益率是整个区间累计，不是年化；2025年以来盈亏来自同一次连续账户。

## 当前完整历史结果

{table(frame, ["label", "extra_fee_bp", "net_pnl", "return_pct", "max_drawdown_pct", "trades", "profit_factor", "review_marked_pnl", "review_closed_trades"], ["策略", "额外bp", "净收益USDT", "累计收益%", "最大回撤%", "完整持仓", "净利润因子", "2025年以来盈亏", "近期完整持仓"])}

![权益与回撤对照](equity_comparison.png)

灰色虚线是执行修复前的历史记录；蓝色为当前原费率，橙色为额外5bp压力。
图为日末值，最大回撤指标按分钟收盘计算，不能代替分钟内最坏路径。

## 与修复前对照

{table(old_frame, ["label", "old_return_pct", "new_return_pct", "old_drawdown_pct", "new_drawdown_pct", "old_trades", "new_trades", "old_invalid_openings", "new_invalid_openings"], ["策略", "旧累计收益%", "当前累计收益%", "旧回撤%", "当前回撤%", "旧持仓数", "当前持仓数", "旧低于最低金额入场", "当前不合规入场"])}

拒绝不满足开仓约束的订单会改变后续权益和定仓路径，差额不能仅解释为“删除这些成交”的加总。
旧报告中的成本压力使用固定路径扣费，未与本轮重新定仓的压力结果混同。

## 胜率、尾部交易与持仓时间

{table(base, ["label", "win_rate_pct", "payoff_ratio", "worst5_pnl", "pnl_without_best5", "max_holding_days", "last_fill"], ["策略", "胜率%", "平均盈亏比", "最差5笔合计USDT", "剔除最佳5笔后盈亏", "最长持仓天数", "最后成交UTC"])}

完整持仓按成交现金流扣除手续费统计。净利润因子为总净盈利/总净亏损绝对值，不能与平均盈亏比混用。
剔除最佳五笔只用于集中度诊断，不是删除交易后的重新定仓回放。
持仓时间按首笔入场到最后退出计算；最后成交可能是实验末尾强制清仓。

## 年度分布

{table(yearly, ["label", "extra_fee_bp", "year", "net_pnl", "return_pct", "max_drawdown_pct", "closed_trades"], ["策略", "额外bp", "年度", "权益变化USDT", "当年收益%", "当年回撤%", "完整持仓数"])}

2026年只到8月30日。年度收益含浮动权益变化，不是每年重置500 USDT；回撤在每年边界重置高水位。

## 核验与研究限制

4次回放均末尾平仓；实际入场的数量和金额约束、订单价格步长以及成交现金流与账户核对通过。
每次回放缺失{audits[0]["missing_minutes"]}分钟，不填造缺口；源文件、数据清单和运行环境在前后保持冻结。
订单事件按实验重复计数：`{dict(reasons_total)}`，明细保存在各运行metrics.json。
撤单/改单事件保留实际回放路径，不将其隐去后报告“零拒单”。
数据已参与研发与选择，属于事后诊断；没有历史资金费率、真实盘口滑点和逐日历史合约规则。
当前快照最低名义金额50 USDT不是历史每日交易规则的复原。两个策略的独立收益不能相加当作组合收益。
本轮没有参数网格或ETH平行检验，因此不证明参数稳定区域或跨市场迁移性。

[判定条件](PROTOCOL.md) · [判定结果](decision.json) · [全部指标](comparison.csv) · [年度指标](annual_comparison.csv) · [执行核验](execution_audit.json) · [冻结清单](manifest.json) · [源码归档](frozen_sources.zip)
"""
    (ROOT / "RESULTS.md").write_text(text)
    web = """<!doctype html><html lang="zh"><meta charset="utf-8"><title>BTC两个多头完整历史复核</title>
<style>body{font:16px system-ui;max-width:1200px;margin:40px auto;padding:0 20px;color:#172331}p{line-height:1.7}img{max-width:100%}table{border-collapse:collapse;font-size:14px}th,td{border:1px solid #ddd;padding:8px}th{background:#f1f4f7}.scroll{overflow:auto}</style>"""
    web += f"<h1>BTC两个多头完整历史复核</h1><p>{html.escape(conclusion)}</p>"
    web += "<p>2021-01-01至2026-08-30；独立500 USDT账户。参数固定，开仓约束已修复；成本压力完整重跑。</p>"
    web += (
        '<div class="scroll">'
        + frame[
            [
                "label",
                "extra_fee_bp",
                "return_pct",
                "max_drawdown_pct",
                "trades",
                "profit_factor",
                "review_marked_pnl",
            ]
        ]
        .rename(
            columns={
                "label": "策略",
                "extra_fee_bp": "额外bp",
                "return_pct": "累计收益%",
                "max_drawdown_pct": "最大回撤%",
                "trades": "完整持仓",
                "profit_factor": "净利润因子",
                "review_marked_pnl": "2025年以来盈亏USDT",
            }
        )
        .to_html(index=False, float_format=lambda x: f"{x:.2f}")
        + "</div>"
    )
    web += '<img src="equity_comparison.png" alt="修复前后权益与回撤比较">'
    web += "<p>属于已见历史诊断，未模拟资金费率、真实滑点和逐日历史合约规则；两策略独立回测。</p>"
    web += '<p><a href="RESULTS.md">完整中文报告、年度分布与核验</a> · <a href="comparison.csv">全部指标</a></p></html>'
    (ROOT / "report.html").write_text(web)
    with zipfile.ZipFile(ROOT / "report_source.zip", "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.write(ROOT / "report.py", "report.py")
    verify(manifest)
    json_write(
        ROOT / "final_validation.json",
        {
            "completed_at": datetime.now(UTC).isoformat(),
            "completed_runs": len(frame),
            "frozen_inputs_verified": True,
            "max_cashflow_error": max(abs(a["cashflow_error"]) for a in audits),
            "invalid_openings": sum(a["invalid_openings"] for a in audits),
            "off_tick_prices": sum(a["off_tick_prices"] for a in audits),
            "report_source_sha256": digest(ROOT / "report.py"),
            "decision": decision,
        },
    )
    print(conclusion, flush=True)
    print(
        frame[
            [
                "label",
                "extra_fee_bp",
                "return_pct",
                "max_drawdown_pct",
                "trades",
                "review_marked_pnl",
            ]
        ].to_string(index=False),
        flush=True,
    )


if __name__ == "__main__":
    main()
