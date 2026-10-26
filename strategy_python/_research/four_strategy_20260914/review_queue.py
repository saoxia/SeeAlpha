"""Run one native 1m review at a time after each strategy's screen completes."""

import json
import subprocess
import sys
import time
from pathlib import Path

from strategy_python._research.four_strategy_20260914.select import shortlist

ROOT = Path(__file__).resolve().parent
pending = ["PBS", "TFL", "TFS", "MRL"]
while pending:
    ready = [
        k
        for k in pending
        if len(list((ROOT / "screen_5m" / k / "train").glob("*/metrics.json"))) == 10
    ]
    if not ready:
        time.sleep(10)
        continue
    key = ready[0]
    names = shortlist(key)
    (ROOT / f"{key}_shortlist.json").write_text(json.dumps(names, indent=2) + "\n")
    print("REVIEW", key, names, flush=True)
    for period in ("train", "validation"):
        with (ROOT / f"{key}_{period}_1m.log").open("a") as log:
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "strategy_python._research.four_strategy_20260914.research",
                    key,
                    period,
                    "--candidates",
                    ",".join(names),
                ],
                stdout=log,
                stderr=subprocess.STDOUT,
                check=True,
            )
    import importlib

    selection_module = importlib.import_module(
        "strategy_python._research.four_strategy_20260914.select"
    )
    importlib.reload(selection_module)
    result = selection_module.select(key)
    (ROOT / f"{key}_selection.json").write_text(json.dumps(result, indent=2) + "\n")
    print("SELECTED", key, result, flush=True)
    pending.remove(key)
print("ALL_REVIEWED", flush=True)
