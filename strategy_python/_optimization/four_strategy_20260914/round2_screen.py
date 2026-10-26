import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
for key, candidates in [
    ("PBS", "broad_rr3,broad_rr5"),
    ("TFL", "extend3_tight,extend4_tight"),
    ("TFS", "extend3_tight,extend4_tight"),
]:
    with (ROOT / f"{key}_round2_screen.log").open("w") as log:
        subprocess.run(
            [
                sys.executable,
                "-m",
                "strategy_python._optimization.four_strategy_20260914.research",
                key,
                "train",
                "--interval",
                "5m",
                "--round2",
                "--candidates",
                candidates,
            ],
            stdout=log,
            stderr=subprocess.STDOUT,
            check=True,
        )
    print("DONE", key, flush=True)
