"""Sequential formal stages; each stage caps native engines at two workers."""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parents[2]


if __name__ == "__main__":
    subprocess.run(
        [sys.executable, "-m", "strategy_python._optimization.mechanism_20260915.verify_runtime"],
        cwd=PROJECT,
        check=True,
    )
    for stage in ("main", "neighbors"):
        print("FORMAL_STAGE", stage, flush=True)
        with (ROOT / f"{stage}_evaluate.log").open("w") as log:
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "strategy_python._optimization.mechanism_20260915.evaluate_runtime",
                    stage,
                ],
                cwd=PROJECT,
                stdout=log,
                stderr=subprocess.STDOUT,
                check=True,
            )
        print("FORMAL_STAGE_COMPLETE", stage, flush=True)
    print("ALL_FORMAL_RUNS_COMPLETE", flush=True)
