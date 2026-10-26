"""Post-run description from immutable fills; does not select or tune a strategy."""

import ast
import json
from collections import Counter
from datetime import UTC, datetime

import numpy as np
import pandas as pd

from .run import ROOT, digest, experiments, verify, write


def exit_reason(tags):
    if pd.isna(tags):
        return "unlabelled_close"
    parsed = ast.literal_eval(tags)
    if not isinstance(parsed, list) or len(parsed) != 1:
        return str(parsed)
    parts = parsed[0].split(":")
    return parts[-1] if "emergency" in parts else parts[-2]


def monthly_uncertainty(path, experiment):
    daily = pd.read_csv(path / "daily_equity.csv", index_col=0, parse_dates=True).equity
    monthly = daily.resample("ME").last()
    returns = monthly.pct_change()
    returns.iloc[0] = monthly.iloc[0] / 500 - 1
    records = []
    for period, series in (
        ("full", returns),
        ("2025+", returns[returns.index.year >= 2025]),
    ):
        values = series.to_numpy() * 100
        rng = np.random.default_rng(20260922)
        starts = rng.integers(0, len(values), size=(2000, (len(values) + 2) // 3))
        indices = ((starts[:, :, None] + np.arange(3)) % len(values)).reshape(2000, -1)
        estimates = values[indices[:, : len(values)]].mean(axis=1)
        lower, upper = np.quantile(estimates, [0.025, 0.975])
        records.append(
            {
                **experiment,
                "period": period,
                "months": len(values),
                "mean_monthly_return_pct": float(values.mean()),
                "lower_95_pct": float(lower),
                "upper_95_pct": float(upper),
            }
        )
    return records


def main():
    verify()
    rows, contributions, audits, uncertainty = [], [], {}, []
    for experiment in experiments():
        identity = experiment["id"]
        output = ROOT / "runs" / identity
        for filename, expected in json.loads((output / "completion.json").read_text()).items():
            if digest(output / filename) != expected:
                raise RuntimeError("Result changed: " + identity)
        metrics = json.loads((output / "metrics.json").read_text())
        uncertainty.extend(monthly_uncertainty(output, experiment))
        trades = pd.read_csv(output / "trades.csv")
        if "id" not in trades and trades.empty:
            trades["id"] = pd.Series(dtype="string")
        try:
            fills = pd.read_csv(output / "fills.csv")
        except pd.errors.EmptyDataError:
            fills = pd.DataFrame()
        if fills.empty:
            fills = pd.DataFrame(columns=["is_reduce_only", "tags", "ts_last", "position_id"])
            fills["is_reduce_only"] = fills["is_reduce_only"].astype(bool)
        exits = fills[fills.is_reduce_only].copy()
        exits["reason"] = exits.tags.map(exit_reason)
        last = exits.sort_values("ts_last").groupby("position_id").tail(1)
        merged = trades.merge(
            last[["position_id", "reason"]],
            left_on="id",
            right_on="position_id",
            validate="one_to_one",
        )
        if len(merged) != len(trades) or abs(merged.pnl.sum() - metrics["net_pnl"]) > 1e-4:
            raise RuntimeError("Exit classification cannot reconcile all trade cash flows")
        counts = exits.reason.value_counts().to_dict()
        events = Counter()
        for reason, count in metrics["order_event_reasons"].items():
            events[reason.split(":", 1)[0]] += count
        audits[identity] = {
            "filled_exit_orders": counts,
            "classified_trades": len(merged),
            "pnl_sum": float(merged.pnl.sum()),
            "order_event_types": dict(events),
            "all_rejections_are_missing_cancel_targets": all(
                reason.startswith("OrderCancelRejected:") and reason.endswith("not found")
                for reason in metrics["order_event_reasons"]
            ),
        }
        for reason, block in merged.groupby("reason"):
            contributions.append(
                {
                    **experiment,
                    "exit_reason": reason,
                    "trades": len(block),
                    "net_pnl": float(block.pnl.sum()),
                    "fees": float(block.fees.sum()),
                }
            )
        wins = trades[trades.pnl > 0]
        losses = trades[trades.pnl < 0]
        rows.append(
            {
                **experiment,
                "winning_trades": len(wins),
                "winning_pnl": float(wins.pnl.sum()),
                "losing_trades": len(losses),
                "losing_pnl": float(losses.pnl.sum()),
                "gross_pnl_before_fees": metrics["net_pnl"] + metrics["fees"],
                "fees": metrics["fees"],
                "net_pnl": metrics["net_pnl"],
                "net_per_trade": metrics["net_pnl"] / len(trades) if len(trades) else None,
            }
        )
    pd.DataFrame(rows).to_csv(ROOT / "payoff_diagnostics.csv", index=False)
    pd.DataFrame(contributions).to_csv(ROOT / "exit_contributions.csv", index=False)
    pd.DataFrame(uncertainty).to_csv(ROOT / "monthly_uncertainty.csv", index=False)
    verify()
    write(
        ROOT / "diagnostics_validation.json",
        {
            "created_at": datetime.now(UTC).isoformat(),
            "script_sha256": digest(__file__),
            "manifest_sha256": digest(ROOT / "manifest.json"),
            "runs": audits,
            "note": (
                "Exit reasons are reconstructed from frozen fills and reconciled to "
                "all completed trade cash flows. These are descriptive contributions, "
                "not causal effects or a new selection rule. Monthly intervals use "
                "three-month circular blocks, 2000 draws and seed 20260922; they "
                "do not correct prior historical model selection. August 2026 is partial."
            ),
            "outputs": {
                name: digest(ROOT / name)
                for name in (
                    "payoff_diagnostics.csv",
                    "exit_contributions.csv",
                    "monthly_uncertainty.csv",
                )
            },
        },
    )
    print("Reconciled exit attribution for", len(rows), "runs")


if __name__ == "__main__":
    main()
