"""Historical replay accounting with typed empty-trade results (see AMENDMENTS.md)."""

import ast

import numpy as np
import pandas as pd
from nautilus_trader.adapters.binance import BINANCE_VENUE

from nautilus_support import USDT


def measure(engine, marks, start, end, output, initial):
    fills = engine.generate_order_fills_report()
    positions = engine.generate_positions_report()
    account = engine.generate_account_report(BINANCE_VENUE)
    for name, frame in (("fills", fills), ("positions", positions), ("account", account)):
        frame.to_csv(output / f"{name}.csv")
    marks = marks[(marks.index >= start) & (marks.index < end)]
    if marks.empty or marks.index.has_duplicates or not marks.index.is_monotonic_increasing:
        raise RuntimeError("Invalid market timestamp coverage")
    events = pd.DataFrame(0.0, index=marks.index, columns=["cash", "qty", "turnover"])
    trades = pd.DataFrame(
        columns=["pnl", "entry_time", "exit_time", "notional", "fees", "residual_qty"]
    ).astype({"pnl": float, "notional": float, "fees": float, "residual_qty": float})
    if not fills.empty:
        if not (fills.status.astype(str) == "FILLED").all():
            raise RuntimeError("Partial fills require incremental accounting")
        fees = fills.commissions.map(
            lambda s: sum(
                float(v.split()[0]) for v in (ast.literal_eval(s) if isinstance(s, str) else s)
            )
        )
        quantities = fills.filled_qty.astype(float)
        signed = np.where(fills.side == "BUY", 1, -1) * quantities
        notional = quantities * fills.avg_px.astype(float)
        flows = -signed * fills.avg_px.astype(float) - fees
        time_index = pd.to_datetime(fills.ts_last, utc=True)
        raw = pd.DataFrame(
            {"cash": flows.to_numpy(), "qty": signed.to_numpy(), "turnover": notional.to_numpy()},
            index=time_index,
        )
        if not raw.index.isin(marks.index).all():
            raise RuntimeError("Fills outside observed market marks")
        events = raw.groupby(level=0).sum().reindex(marks.index, fill_value=0.0)
        grouped = pd.DataFrame(
            {
                "id": fills.position_id,
                "pnl": flows,
                "time": time_index,
                "notional": notional,
                "fees": fees,
                "qty": signed,
            }
        )
        trades = grouped.groupby("id").agg(
            pnl=("pnl", "sum"),
            entry_time=("time", "min"),
            exit_time=("time", "max"),
            notional=("notional", "sum"),
            fees=("fees", "sum"),
            residual_qty=("qty", "sum"),
        )
        if (trades.residual_qty.abs() > 1e-7).any():
            raise RuntimeError("Unclosed position cash flow")
    cumulative = events.cumsum()
    equity = initial + cumulative.cash + cumulative.qty * marks
    if abs(cumulative.qty.iloc[-1]) > 1e-7:
        raise RuntimeError("Unclosed final exposure")
    balance = engine.cache.account_for_venue(BINANCE_VENUE).balance_total(USDT).as_double()
    pnl = float(balance - initial)
    if abs(equity.iloc[-1] - balance) > 1e-4 or abs(trades.pnl.sum() - pnl) > 1e-4:
        raise RuntimeError("Account / marked equity / trade cash flow mismatch")
    dd = 1 - equity / equity.cummax().clip(lower=initial)
    wins, losses = trades.pnl[trades.pnl > 0], trades.pnl[trades.pnl < 0]
    annual = equity.resample("YE").last()
    change = annual.diff()
    change.iloc[0] = annual.iloc[0] - initial
    review_start = pd.Timestamp("2025-01-01", tz="UTC")
    prior = equity[equity.index < review_start]
    later = equity[equity.index >= review_start]
    review_pnl = float(later.iloc[-1] - prior.iloc[-1]) if len(prior) and len(later) else None
    result = {
        "net_pnl": pnl,
        "return_pct": pnl / initial * 100,
        "max_drawdown_pct": float(dd.max() * 100),
        "trades": len(trades),
        "fees": float(trades.fees.sum()),
        "turnover": float(trades.notional.sum()),
        "profit_factor": float(wins.sum() / -losses.sum()) if len(losses) else None,
        "payoff_ratio": float(wins.mean() / -losses.mean()) if len(wins) and len(losses) else None,
        "win_rate_pct": float((trades.pnl > 0).mean() * 100) if len(trades) else None,
        "top5_profit": float(wins.nlargest(5).sum()),
        "pnl_without_best5": float(pnl - wins.nlargest(5).sum()),
        "worst5_pnl": float(losses.nsmallest(5).sum()),
        "annual_marked_pnl": {str(k.year): float(v) for k, v in change.items()},
        "review_marked_pnl": review_pnl,
        "review_closed_trades": int((trades.exit_time >= review_start).sum()) if len(trades) else 0,
        "expected_minutes": int((end - start).total_seconds() / 60),
        "observed_minutes": len(marks),
        "missing_minutes": int((end - start).total_seconds() / 60) - len(marks),
        "first_bar": str(marks.index[0]),
        "last_bar": str(marks.index[-1]),
        "cashflow_error": float(trades.pnl.sum() - pnl),
    }
    trades.to_csv(output / "trades.csv")
    pd.DataFrame({"equity": equity, "drawdown_pct": dd * 100}).resample("1D").last().to_csv(
        output / "daily_equity.csv"
    )
    annual_rows = []
    for year, block in equity.groupby(equity.index.year):
        before = equity[equity.index < block.index[0]]
        base = float(before.iloc[-1]) if len(before) else initial
        annual_rows.append(
            {
                "year": year,
                "net_pnl": float(block.iloc[-1] - base),
                "return_pct": float((block.iloc[-1] / base - 1) * 100),
                "max_drawdown_pct": float(
                    (1 - block / block.cummax().clip(lower=base)).max() * 100
                ),
                "closed_trades": int((trades.exit_time.dt.year == year).sum())
                if len(trades)
                else 0,
            }
        )
    pd.DataFrame(annual_rows).to_csv(output / "annual.csv", index=False)
    return result
