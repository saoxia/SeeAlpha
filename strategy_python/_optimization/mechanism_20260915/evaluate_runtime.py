"""Bounded-memory candidate/control evaluations, maximum two native engines."""

import argparse
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parents[2]
REGISTRY = json.loads((ROOT / "runtime_strategies.json").read_text())


def execute(case):
    key, period, variant, mode = case
    name = f"{key}_{period}_{variant}_{mode}"
    command = [
        sys.executable,
        "-m",
        "strategy_python._optimization.mechanism_20260915.run",
        key,
        period,
        "--variant",
        variant,
        "--mode",
        mode,
    ]
    if period == "stress":
        command += ["--batch-days", "7"]
    print("START", name, flush=True)
    with (ROOT / f"{name}.log").open("w") as log:
        subprocess.run(command, cwd=PROJECT, stdout=log, stderr=subprocess.STDOUT, check=True)
    print("DONE", name, flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=["stress", "main", "neighbors"])
    args = parser.parse_args()
    if args.stage == "stress":
        cases = [
            (key, "stress", "main", "stream")
            for key, info in REGISTRY.items()
            if info["kind"] == "mechanism"
        ]
        cases += [
            ("BTC_" + slot, "stress", "main", "normal")
            for slot in ("MRL_original", "TFL", "TFS", "PBS")
        ]
    elif args.stage == "main":
        cases = [
            (key, period, "main", "stream")
            for key in REGISTRY
            for period in ("validation", "review")
        ]
        cases += [
            (asset + "_" + slot, period, "flat_risk", "stream")
            for asset in ("ETH", "BTC")
            for slot in ("MRL_original", "MRL_optimized")
            for period in ("validation", "review")
        ]
    else:
        cases = [
            (asset + "_" + slot, period, variant, "stream")
            for asset in ("ETH", "BTC")
            for slot in ("TFL", "TFS", "PBS")
            for period in ("validation", "review")
            for variant in (
                ("buffer90",)
                if slot == "PBS"
                else ("stop70", "stop90", "activation40", "activation60")
            )
        ]
    (ROOT / f"{args.stage}_cases.json").write_text(json.dumps(cases, indent=2) + "\n")
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(execute, cases))
    print("ALL_COMPLETED", args.stage, len(cases), flush=True)


if __name__ == "__main__":
    main()
