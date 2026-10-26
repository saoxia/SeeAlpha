"""Derive payoff and turnover from frozen final fills and closed positions."""

import ast
import csv
import json
import math
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LABELS = {
    "MRL": "均值回归做多",
    "TFL": "趋势跟随做多",
    "TFS": "趋势跟随做空",
    "PBS": "小时反弹做空",
}


def main():
    rows = []
    for key in LABELS:
        selected = json.loads((ROOT / f"{key}_selection.json").read_text())["selected"]
        for period in ("full", "holdout"):
            for version, folder, candidate in (
                ("baseline", "runs", "baseline"),
                ("optimized", "frozen_runs", selected),
            ):
                source = ROOT / folder / key / period / candidate
                metrics = json.loads((source / "metrics.json").read_text())
                with (source / "positions.csv").open() as stream:
                    positions = list(csv.DictReader(stream))
                with (source / "fills.csv").open() as stream:
                    fills = list(csv.DictReader(stream))
                assert all(p["side"] == "FLAT" for p in positions)
                assert len({f["client_order_id"] for f in fills}) == len(fills)
                assert all(f["status"] == "FILLED" for f in fills)
                cash = defaultdict(Decimal)
                quantity = defaultdict(Decimal)
                for fill in fills:
                    qty = Decimal(fill["filled_qty"])
                    price = Decimal(fill["avg_px"])
                    sign = 1 if fill["side"] == "BUY" else -1
                    fees = sum(Decimal(f.split()[0]) for f in ast.literal_eval(fill["commissions"]))
                    cash[fill["position_id"]] += -sign * qty * price - fees
                    quantity[fill["position_id"]] += sign * qty
                assert all(q == 0 for q in quantity.values())
                assert set(cash) == {p["position_id"] for p in positions}
                pnl = [float(p) for p in cash.values()]
                reported_pnl = sum(float(p["realized_pnl"].split()[0]) for p in positions)
                wins = [p for p in pnl if p > 0]
                losses = [p for p in pnl if p < 0]
                average_win = sum(wins) / len(wins)
                average_loss = -sum(losses) / len(losses)
                turnover = sum(float(f["filled_qty"]) * float(f["avg_px"]) for f in fills)
                assert len(pnl) == metrics["trades"]
                assert math.isclose(sum(pnl), metrics["net_pnl"], abs_tol=0.0001)
                assert math.isclose(
                    metrics["net_pnl"] - turnover * 0.0005,
                    metrics["stress"]["5"]["net_pnl"], abs_tol=0.0001,
                )
                rows.append({
                    "strategy": key, "period": period, "version": version,
                    "candidate": candidate, "net_pnl_usdt": sum(pnl),
                    "trades": len(pnl), "winning_trades": len(wins),
                    "losing_trades": len(losses), "average_win_usdt": average_win,
                    "average_loss_usdt": average_loss,
                    "payoff_ratio": average_win / average_loss,
                    "profit_factor": sum(wins) / -sum(losses),
                    "position_report_pnl_difference_usdt": reported_pnl - sum(pnl),
                    "max_drawdown_pct": metrics["max_drawdown_pct"],
                    "turnover_usdt": turnover,
                })
    with (ROOT / "trade_metrics.csv").open("w") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    (ROOT / "trade_metrics.json").write_text(json.dumps(rows, indent=2) + "\n")
    lines = [
        "# 盈亏比、最大回撤与总成交金额", "",
        "每项均为独立 500 USDT 初始资金；单元格为原版 → 新版。",
        "每笔交易按持仓编号聚合成交现金流并扣除手续费，核对其合计与账户净收益一致。盈亏比 = 平均盈利交易净收益 ÷ 平均亏损交易净亏损绝对值（不含零盈亏交易）。",
        "最大回撤按每分钟收盘盯市权益计算，包含浮动盈亏；不是分钟内最差价格回撤。",
        "总成交金额 = 所有成交数量 × 成交价格之和，包含开仓、加仓、减仓、平仓两侧；不是本金或保证金。",
        "净收益已扣模拟手续费，未含历史资金费率与真实滑点。", "",
    ]
    for period, dates in (
        ("full", "全历史：2021-01-01 至 2026-08-31"),
        ("holdout", "本轮留出期：2025-01-01 至 2026-08-31"),
    ):
        lines += [dates, "", "| 策略 | 盈亏比 | 最大回撤 | 总成交金额（USDT） |",
                  "|---|---:|---:|---:|"]
        for key, label in LABELS.items():
            old, new = [r for r in rows if r["strategy"] == key and r["period"] == period]
            lines.append(
                f"| {label} | {old['payoff_ratio']:.4f} → {new['payoff_ratio']:.4f} | "
                f"{old['max_drawdown_pct']:.2f}% → {new['max_drawdown_pct']:.2f}% | "
                f"{old['turnover_usdt']:,.2f} → {new['turnover_usdt']:,.2f} |"
            )
        lines.append("")
    lines += [
        "均值回归持仓报告汇总与账户净收益存在小额差异（全历史原版 -0.6826、新版 -1.8722 USDT）；此处采用与账户一致的成交现金流口径。",
        "留出期曾被原策略研究使用，并非完全未见样本；新版参数在本轮查看留出结果前固定。",
        "盈亏比不等于利润因子（总盈利 ÷ 总亏损绝对值）；两者及平均单笔盈亏均保存在 trade_metrics.csv。",
        "已逐项核对：成交额与原报告 5bp 成本敏感性一致，逐笔净收益之和与报告净收益一致。", "",
    ]
    report = "\n".join(lines)
    (ROOT / "TRADE_METRICS.md").write_text(report)
    print(report)


if __name__ == "__main__":
    main()
