"""Replay all eight actual final packages and compare every execution against selection."""

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd

from .evaluate_followups import execute

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parents[2]


def main():
    profiles = json.loads((ROOT / "FINAL_STRATEGIES.json").read_text())
    assert len(profiles) == 8
    frozen = json.loads((ROOT / "frozen_full_cycle.json").read_text())
    paths = [
        ROOT / name
        for name in [
            "final_run.py",
            "final_factory.py",
            "final_strategies.json",
            "final_runtime_strategies.json",
            "final_experiments.json",
            "FINAL_STRATEGIES.json",
        ]
    ]
    for p in profiles:
        directory = PROJECT / p["package"].replace(".", "/")
        paths += list(directory.glob("*.py")) + [directory / "profile.json"]
    for p in paths:
        frozen[str(p.relative_to(PROJECT))] = hashlib.sha256(p.read_bytes()).hexdigest()
    target = ROOT / "frozen_final_runtime.json"
    if target.exists():
        assert json.loads(target.read_text()) == frozen
    else:
        target.write_text(json.dumps(frozen, indent=2) + "\n")
    for source, digest in frozen.items():
        assert hashlib.sha256((PROJECT / source).read_bytes()).hexdigest() == digest, source
    cases = [("final_run", p["slot"], "full", "main") for p in profiles]
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(execute, cases))
    verified = []
    cols = ["ts_last", "side", "filled_qty", "avg_px", "commissions"]
    for p in profiles:
        actual = ROOT / "final_runs/full" / p["slot"]
        reference = PROJECT / p["validated_research_run"]
        a = json.loads((actual / "metrics.json").read_text())
        b = json.loads((reference / "metrics.json").read_text())
        params = json.loads((actual / "parameters.json").read_text())
        assert params["config"] == p["config"]
        assert (
            params["start"] == "2021-01-01T00:00:00+00:00"
            and params["end"] == "2026-08-31T00:00:00+00:00"
        )
        assert params["runner_sha256"] == frozen[str((ROOT / "final_run.py").relative_to(PROJECT))]
        for name in ["net_pnl", "max_drawdown_pct", "fees", "cashflow_trades", "turnover_usdt"]:
            assert abs(a[name] - b[name]) < 1e-7, (p["slot"], name, a[name], b[name])
        assert (
            a["off_tick_order_price_fields"] == 0 and abs(a["cashflow_reconciliation_error"]) < 1e-4
        )
        pd.testing.assert_frame_equal(
            pd.read_csv(actual / "fills.csv")[cols],
            pd.read_csv(reference / "fills.csv")[cols],
            check_exact=True,
        )
        pd.testing.assert_frame_equal(
            pd.read_csv(actual / "daily_equity.csv"),
            pd.read_csv(reference / "daily_equity.csv"),
            check_exact=True,
        )
        verified.append(
            {
                "slot": p["slot"],
                "package": p["package"],
                "config_exact": True,
                "full_history_fills_exact": True,
                "daily_equity_exact": True,
                "net_pnl": a["net_pnl"],
                "max_drawdown_pct": a["max_drawdown_pct"],
            }
        )
    for source, digest in frozen.items():
        assert hashlib.sha256((PROJECT / source).read_bytes()).hexdigest() == digest, source
    (ROOT / "FINAL_VERIFICATION.json").write_text(
        json.dumps({"runs": verified, "all_prior_files_unchanged": True}, indent=2) + "\n"
    )
    print("FINAL_NATIVE_VERIFICATION_COMPLETE", len(verified), flush=True)


if __name__ == "__main__":
    main()
