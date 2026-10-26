"""Explain realized trade payoff structure without selecting new parameters."""

import ast
import json
import math

import numpy as np
import pandas as pd

from strategy_python._research.mechanism_20260915.data import ROOT


def shape(path):
    fills = pd.read_csv(path)
    if fills.empty:
        return {}
    fees = fills.commissions.map(
        lambda value: sum(float(x.split()[0]) for x in ast.literal_eval(value))
    )
    notional = fills.filled_qty.astype(float) * fills.avg_px.astype(float)
    frame = fills.assign(
        notional=notional, cashflow=np.where(fills.side == "SELL", 1, -1) * notional - fees
    )
    trades = []
    for _, group in frame.groupby("position_id", sort=False):
        group = group.sort_values("ts_last", kind="stable")
        entry_side = group.side.iloc[0]
        entry_notional = group.loc[group.side == entry_side, "notional"].sum()
        trades.append(
            {"pnl": group.cashflow.sum(), "unit_return": group.cashflow.sum() / entry_notional}
        )
    trades = pd.DataFrame(trades)
    winners, losers = trades[trades.pnl > 0], trades[trades.pnl < 0]
    top_n = max(1, math.ceil(len(trades) * 0.05))
    result = {
        "trades": len(trades),
        "win_rate": len(winners) / len(trades),
        "net_pnl": trades.pnl.sum(),
        "unit_mean": trades.unit_return.mean(),
        "unit_winner_mean": winners.unit_return.mean(),
        "unit_loser_mean": losers.unit_return.mean(),
        "unit_payoff": winners.unit_return.mean() / -losers.unit_return.mean()
        if len(winners) and len(losers)
        else np.nan,
        "break_even_net_loss_at_observed_win_structure": len(winners)
        / len(losers)
        * winners.unit_return.mean()
        if len(losers)
        else np.nan,
        "net_without_best_5pct": trades.pnl.sum() - trades.pnl.nlargest(top_n).sum(),
        "removed_best_trades": top_n,
    }
    return result


def main():
    rows = []
    for asset in ("ETH", "BTC"):
        for family in ("TFL", "TFS"):
            for period in ("validation", "review"):
                for key in (
                    f"fixed_{asset}_{family}_original",
                    f"fixed_{asset}_{family}_optimized",
                    f"{asset}_{family}",
                ):
                    directory = ROOT / "runs" / period / key
                    result = shape(directory / "fills.csv")
                    metric = json.loads((directory / "metrics.json").read_text())
                    assert abs(result.get("net_pnl", 0) - metric["net_pnl"]) < 0.0001
                    rows.append(
                        {"asset": asset, "family": family, "period": period, "key": key, **result}
                    )
    pd.DataFrame(rows).to_csv(ROOT / "trade_payoff_shape.csv", index=False)
    lines = [
        "# 趋势策略成交结构",
        "",
        "用实际成交现金流扣除费用，按持仓汇总。每笔收益再除以入场总名义金额，帮助区分仓位大小与退出表现；这不是保证金收益率。",
        "",
        "| 资产/方向/区间 | 版本 | 胜率 | 平均盈利交易收益率 | 平均亏损交易收益率 | 单位名义盈亏比 | 去掉最好5%交易后的净收益 |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        lines.append(
            f"| {row['asset']}/{row['family']}/{row['period']} | {row['key']} | {row.get('win_rate', np.nan):.1%} | {row.get('unit_winner_mean', np.nan):.2%} | {row.get('unit_loser_mean', np.nan):.2%} | {row.get('unit_payoff', np.nan):.2f} | {row.get('net_without_best_5pct', np.nan):.2f} |"
        )
    lines += [
        "",
        "[完整数据](trade_payoff_shape.csv)。去掉最好5%是描述性集中度压力检查，没有重新定仓，不是另一个可交易策略。少量大盈利贡献主要收益时，可以支持“小亏损换取大趋势”的经济解释，但不能据此证明0.5%、2%、3%或4%是唯一最优值。",
        "",
        "若新止损覆盖更多恢复路径，却使平均亏损扩大、平均盈利缩小或盈亏比下降，就不能把恢复覆盖率的提高等同于策略优化。还应结合实际交易数、跨时期结果和参数邻域，而不能只看胜率。",
        "",
        "CSV另列在观察到的胜率p和平均净盈利W保持不变时的盈亏平衡平均净亏损p×W/(1−p)。这能解释小止损如何约束亏损，但改变止损也会改变p和W，所以不能把该恒等式误用来反推唯一最优止损值。",
    ]
    (ROOT / "TRADE_PAYOFF_EVIDENCE.md").write_text("\n".join(lines) + "\n")
    print("PAYOFF_SHAPES_COMPLETE", len(rows))


if __name__ == "__main__":
    main()
