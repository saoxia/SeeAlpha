"""Select using development and validation only. Never reads holdout or full results."""

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def shortlist(key, limit=3):
    trials = []
    for path in (ROOT / "screen_5m" / key / "train").glob("*/metrics.json"):
        metrics = json.loads(path.read_text())
        if path.parent.name != "baseline" and metrics["trades"] >= 20:
            trials.append((metrics["net_pnl"], path.parent.name))
    return ["baseline"] + [name for _, name in sorted(trials, reverse=True)[:limit]]


def select(key):
    train_root = ROOT / "runs" / key / "train"
    val_root = ROOT / "runs" / key / "validation"
    base_train = json.loads((train_root / "baseline/metrics.json").read_text())
    base_val = json.loads((val_root / "baseline/metrics.json").read_text())
    eligible = []
    decisions = {}
    for path in val_root.glob("*/metrics.json"):
        name = path.parent.name
        if name == "baseline":
            continue
        val = json.loads(path.read_text())
        train = json.loads((train_root / name / "metrics.json").read_text())
        rules = {
            "train_net_above_baseline": train["net_pnl"] > base_train["net_pnl"],
            "validation_net_above_baseline": val["net_pnl"] > base_val["net_pnl"],
            "validation_dd_guard": val["max_drawdown_pct"] <= base_val["max_drawdown_pct"] + 5,
            "train_at_least_two_years_improve": sum(
                v > base_train["annual_marked_pnl"].get(y, 0) + 0.01
                for y, v in train["annual_marked_pnl"].items()
            )
            >= 2,
            "adequate_trades": train["trades"] >= 20 and val["trades"] >= 5,
        }
        decisions[name] = rules
        if all(rules.values()):
            eligible.append((val["net_pnl"], name))
    selected = max(eligible)[1] if eligible else "baseline"
    return {
        "selected": selected,
        "criteria": decisions,
        "selection_data": "2021-2023 train and 2024 validation only",
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=["shortlist", "select"])
    parser.add_argument("strategy")
    args = parser.parse_args()
    result = shortlist(args.strategy) if args.stage == "shortlist" else select(args.strategy)
    print(json.dumps(result, indent=2))
    if args.stage == "select":
        (ROOT / f"{args.strategy}_selection.json").write_text(json.dumps(result, indent=2) + "\n")
