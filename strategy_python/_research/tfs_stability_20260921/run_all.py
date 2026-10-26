"""Bounded local workers, resumable outputs, and exact historical center reconciliation."""

import argparse
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

from .study import ROOT, verify


def execute(row):
    logs = ROOT / "logs"
    logs.mkdir(exist_ok=True)
    env = dict(os.environ, OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1", MKL_NUM_THREADS="1")
    with (logs / (row["id"] + ".log")).open("w") as output:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "strategy_python._research.tfs_stability_20260921.study",
                "run",
                row["id"],
            ],
            stdout=output,
            stderr=subprocess.STDOUT,
            env=env,
            check=False,
        )
    if result.returncode:
        raise RuntimeError(f"{row['id']} failed; see {logs / (row['id'] + '.log')}")
    return row["id"]


def reconcile_center():
    import pandas as pd

    current = json.loads((ROOT / "runs/BTC_s100_l80_fee0/metrics.json").read_text())
    old_path = ROOT.parent / "btc_cycle_20260915/final_runs/full/TFS_original"
    old = json.loads((old_path / "metrics.json").read_text())
    fills = pd.read_csv(ROOT / "runs/BTC_s100_l80_fee0/fills.csv")
    entries = fills[~fills.is_reduce_only.astype(bool)]
    invalid = int(
        (entries.filled_qty.astype(float) * entries.avg_px.astype(float) < 50 - 1e-8).sum()
    )
    if invalid or abs(current["cashflow_error"]) > 1e-6:
        raise RuntimeError("Corrected center failed opening bounds/account reconciliation")
    (ROOT / "center_reconciliation.json").write_text(
        json.dumps(
            {
                "historical_run": str(old_path),
                "pre_fix_exact_reconciliation": "before_constraints/center_reconciliation.json",
                "corrected_opening_fills_below_minimum": invalid,
                "cashflow_error": current["cashflow_error"],
                "before_after": {
                    key: [old[key], current[key]]
                    for key in ("net_pnl", "max_drawdown_pct", "fees", "trades")
                },
                "reason_for_difference": "Opening instrument constraints now enforced; alpha and risk parameters unchanged",
            },
            indent=2,
        )
        + "\n"
    )


def main(workers):
    manifest = json.loads((ROOT / "manifest.json").read_text())
    verify(manifest, data=True)
    center = next(r for r in manifest["experiments"] if r["id"] == "BTC_s100_l80_fee0")
    execute(center)
    reconcile_center()
    print("CORRECTED CENTER VERIFIED: opening minimum and account reconciliation", flush=True)
    rest = [r for r in manifest["experiments"] if r != center]
    rest.sort(
        key=lambda r: (
            r["kind"] == "grid" and not (r["stop_scale"] == 1 and r["lock"] == 0.8),
            r["id"],
        )
    )
    completed = [center["id"]]
    failures = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(execute, row): row for row in rest}
        for future in as_completed(futures):
            row = futures[future]
            try:
                completed.append(future.result())
                print(f"COMPLETE {len(completed)}/106 {row['id']}", flush=True)
            except (OSError, RuntimeError) as exc:
                failures.append(str(exc))
                print("FAILED", row["id"], str(exc), flush=True)
            progress = {"completed": completed, "failed": failures, "total": 106}
            temp = ROOT / "progress.tmp"
            temp.write_text(json.dumps(progress, indent=2) + "\n")
            temp.replace(ROOT / "progress.json")
    verify(manifest, data=True)
    if failures:
        raise RuntimeError("Incomplete study: " + "; ".join(failures))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    if not 1 <= args.workers <= 8:
        parser.error("Use 1 to 8 local workers")
    main(args.workers)
