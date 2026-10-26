"""Use a freed review slot for the best predeclared second-round candidate per strategy."""

import json
import subprocess
import sys
import time
from pathlib import Path

from strategy_python._research.four_strategy_20260914.select import select

ROOT = Path(__file__).resolve().parent
while "SELECTED" not in (ROOT / "review_tfs.log").read_text():
    time.sleep(10)
for key in ("PBS", "TFL", "TFS"):
    while len(list((ROOT / "screen_5m_round2" / key / "train").glob("*/metrics.json"))) < 2:
        time.sleep(10)
    trials = [
        (json.loads(p.read_text())["net_pnl"], p.parent.name)
        for p in (ROOT / "screen_5m_round2" / key / "train").glob("*/metrics.json")
    ]
    name = max(trials)[1]
    (ROOT / f"{key}_round2_shortlist.json").write_text(json.dumps([name]) + "\n")
    print("REVIEW", key, name, flush=True)
    for period in ("train", "validation"):
        with (ROOT / f"{key}_round2_{period}_1m.log").open("a") as log:
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "strategy_python._research.four_strategy_20260914.research",
                    key,
                    period,
                    "--candidates",
                    name,
                ],
                stdout=log,
                stderr=subprocess.STDOUT,
                check=True,
            )
    result = select(key)
    (ROOT / f"{key}_selection.json").write_text(json.dumps(result, indent=2) + "\n")
    print("SELECTED", key, result, flush=True)
print("ALL_REVIEWED", flush=True)
