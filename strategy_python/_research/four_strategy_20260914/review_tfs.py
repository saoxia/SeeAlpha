"""A second bounded review worker; per-trial locks prevent duplicate execution."""

import json
import subprocess
import sys
import time
from pathlib import Path

from strategy_python._research.four_strategy_20260914.select import select, shortlist

ROOT = Path(__file__).resolve().parent
while len(list((ROOT / "screen_5m/TFS/train").glob("*/metrics.json"))) < 10:
    time.sleep(5)
names = shortlist("TFS")
(ROOT / "TFS_shortlist.json").write_text(json.dumps(names, indent=2) + "\n")
for period in ("train", "validation"):
    with (ROOT / f"TFS_{period}_1m.log").open("a") as log:
        subprocess.run(
            [
                sys.executable,
                "-m",
                "strategy_python._research.four_strategy_20260914.research",
                "TFS",
                period,
                "--candidates",
                ",".join(names),
            ],
            stdout=log,
            stderr=subprocess.STDOUT,
            check=True,
        )
result = select("TFS")
(ROOT / "TFS_selection.json").write_text(json.dumps(result, indent=2) + "\n")
print("SELECTED", result, flush=True)
