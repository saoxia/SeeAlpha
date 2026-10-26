"""Post-run description from immutable fills; does not select or tune a strategy."""

import ast
import json
from collections import Counter
from datetime import UTC, datetime

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


def main():
    verify()
    rows, contributions, audits = [], [], {}
    for experiment in experiments():
        identity = experiment["id"]
        output = ROOT / "runs" / identity
        for filename, expected in json.loads((output / "completion.json").read_text()).items():
            if digest(output / filename) != expected:
                raise RuntimeError("Result changed: " + identity)
        metrics = json.loads((output / "metrics.json").read_text())
        trades = pd.read_csv(output / "trades.csv")
        fills = pd.read_csv(output / "fills.csv")
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
    verify()
    write(
        ROOT / "diagnostics_validation.json",
        {
            "created_at": datetime.now(UTC).isoformat(),
            "script_sha256": digest(__file__),
            "manifest_sha256": digest(ROOT / "manifest.json"),
            "runs": audits,
            "note": (
                "Use these CSV-derived exit counts. The frozen runner's optional "
                "exit_order_tags counter does not normalize list-valued tags or "
                "take-profit/hard-stop names; it is not used in returns or decision gates. "
                "All frozen run files remain unchanged."
            ),
            "outputs": {
                name: digest(ROOT / name)
                for name in ("payoff_diagnostics.csv", "exit_contributions.csv")
            },
        },
    )
    print("Reconciled exit attribution for", len(rows), "runs")


if __name__ == "__main__":
    main()
