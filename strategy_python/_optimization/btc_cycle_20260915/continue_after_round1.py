"""Serial post-round-one stages; invoke only when its verified process has ended."""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parents[2]
STAGES = [
    ("round1_report", ["round1_report"]),
    ("next_smoke", ["evaluate_followups", "next_smoke"]),
    ("verify_next_smoke", ["verify_next_smoke"]),
    ("crosscheck", ["evaluate_followups", "crosscheck"]),
    ("crosscheck_report", ["crosscheck_report"]),
    ("next_develop", ["evaluate_followups", "next_develop"]),
    ("next_report", ["next_report"]),
    ("next_crosscheck", ["evaluate_followups", "next_crosscheck"]),
    ("next_crosscheck_report", ["crosscheck_report", "--next"]),
]

if __name__ == "__main__":
    assert "ALL_COMPLETE round1 38" in (ROOT / "round1.log").read_text(), (
        "Prior stage is not complete"
    )
    for label, args in STAGES:
        print("STAGE_START", label, flush=True)
        with (ROOT / (label + ".log")).open("w") as log:
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "strategy_python._optimization.btc_cycle_20260915." + args[0],
                    *args[1:],
                ],
                cwd=PROJECT,
                stdout=log,
                stderr=subprocess.STDOUT,
                check=True,
            )
        print("STAGE_DONE", label, flush=True)
    print("POST_ROUND1_COMPLETE", flush=True)
