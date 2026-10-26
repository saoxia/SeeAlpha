"""Finish MRL's bounded second round, then freeze all choices before holdout."""

import importlib
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
while "ALL_REVIEWED" not in (ROOT / "review_queue.log").read_text():
    time.sleep(10)
while len(list((ROOT / "screen_5m_round2/MRL/train").glob("*/metrics.json"))) < 2:
    time.sleep(10)
trials = [
    (json.loads(p.read_text())["net_pnl"], p.parent.name)
    for p in (ROOT / "screen_5m_round2/MRL/train").glob("*/metrics.json")
]
name = max(trials)[1]
(ROOT / "MRL_round2_shortlist.json").write_text(json.dumps([name]) + "\n")
print("REVIEW MRL", name, flush=True)
for period in ("train", "validation"):
    with (ROOT / f"MRL_round2_{period}_1m.log").open("a") as log:
        subprocess.run(
            [
                sys.executable,
                "-m",
                "strategy_python._research.four_strategy_20260914.research",
                "MRL",
                period,
                "--candidates",
                name,
            ],
            stdout=log,
            stderr=subprocess.STDOUT,
            check=True,
        )
while "ALL_REVIEWED" not in (ROOT / "round2_review.log").read_text():
    time.sleep(10)
selection = importlib.import_module("strategy_python._research.four_strategy_20260914.select")
for key in ("MRL", "TFL", "TFS", "PBS"):
    result = selection.select(key)
    (ROOT / f"{key}_selection.json").write_text(json.dumps(result, indent=2) + "\n")
    print("FINAL_SELECTION", key, result, flush=True)
subprocess.run(
    [sys.executable, "-m", "strategy_python._research.four_strategy_20260914.materialize"],
    check=True,
)
print("FROZEN", flush=True)
