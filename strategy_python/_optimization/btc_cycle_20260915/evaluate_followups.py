"""Explicit follow-up stages. Start only after the prior native stage is terminal."""

import argparse
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parents[2]
UNIQUE = ["MRL_original", "MRL_optimized", "TFL_original", "TFS_original", "PBS_original"]


def execute(item):
    module, slot, period, variant = item
    tag = f"{slot}_{period}_{variant}"
    print("START", tag, flush=True)
    with (ROOT / (tag + ".log")).open("w") as log:
        args = [
            sys.executable,
            "-m",
            "strategy_python._optimization.btc_cycle_20260915." + module,
            slot,
            period,
            "--variant",
            variant,
        ]
        if period == "stress":
            args += ["--batch-days", "7"]
        subprocess.run(args, cwd=PROJECT, stdout=log, stderr=subprocess.STDOUT, check=True)
    print("DONE", tag, flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "stage", choices=["crosscheck", "next_smoke", "next_develop", "next_crosscheck"]
    )
    a = parser.parse_args()
    if a.stage == "crosscheck":
        shortlist = json.loads((ROOT / "round1_shortlist.json").read_text())
        choices = [(s, "main") for s in UNIQUE] + [(x["slot"], x["variant"]) for x in shortlist]
        cases = [("run", s, p, v) for s, v in choices for p in ["validation", "review"]]
    elif a.stage == "next_crosscheck":
        shortlist = json.loads((ROOT / "next_shortlist.json").read_text())
        choices = [(s, "next_base") for s in UNIQUE] + [
            (x["slot"], x["variant"]) for x in shortlist
        ]
        cases = [("next_run", s, p, v) for s, v in choices for p in ["validation", "review"]]
    elif a.stage == "next_smoke":
        choices = [(s, "next_base") for s in UNIQUE] + [
            ("MRL_original", "space_units1_same_risk"),
            ("MRL_optimized", "space_units2_same_risk"),
            ("MRL_original", "next_time24_rearm"),
            ("TFL_original", "space_budget1.5"),
            ("TFS_original", "space_margin10"),
            ("PBS_original", "space_margin2"),
        ]
        cases = [("next_run", s, "stress", v) for s, v in choices]
    else:
        exp = json.loads((ROOT / "next_experiments.json").read_text())
        cases = [("next_run", s, "develop", v) for s in UNIQUE for v in exp[s]]
    output = ROOT / (a.stage + "_cases.json")
    if output.exists():
        assert json.loads(output.read_text()) == [list(c) for c in cases], (
            "Stage definition changed"
        )
    else:
        output.write_text(json.dumps(cases, indent=2) + "\n")
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(execute, cases))
    print("ALL_COMPLETE", a.stage, len(cases), flush=True)


if __name__ == "__main__":
    main()
