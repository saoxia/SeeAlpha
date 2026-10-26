"""Evaluate frozen delivered defaults after tests pass; never changes selections."""

import json
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent
while "FROZEN" not in (ROOT / "freeze_queue.log").read_text():
    time.sleep(10)
while not (ROOT / "tests.ok").exists():
    time.sleep(10)
selected = {
    k: json.loads((ROOT / f"{k}_selection.json").read_text())["selected"]
    for k in ("MRL", "TFL", "TFS", "PBS")
}
(ROOT / "frozen_choices.json").write_text(json.dumps(selected, indent=2) + "\n")
jobs = [
    (key, period, opt)
    for period in ("full", "holdout")
    for key in selected
    for opt in (False, True)
]


def job(item):
    key, period, opt = item
    name = selected[key] if opt else "baseline"
    command = [
        sys.executable,
        "-m",
        "strategy_python._optimization.four_strategy_20260914.streaming",
        key,
        period,
        "--candidate",
        name,
    ]
    if opt:
        command.append("--optimized")
    with (ROOT / f"final_{key}_{period}_{'new' if opt else 'original'}.log").open("w") as log:
        subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=True)
    return item


with ThreadPoolExecutor(max_workers=3) as pool:
    futures = [pool.submit(job, item) for item in jobs]
    for future in as_completed(futures):
        print("DONE", future.result(), flush=True)
assert selected == {
    k: json.loads((ROOT / f"{k}_selection.json").read_text())["selected"] for k in selected
}
subprocess.run(
    [sys.executable, "-m", "strategy_python._optimization.four_strategy_20260914.write_report"],
    check=True,
)
print("ALL_FINAL_EVALUATIONS_COMPLETE", flush=True)
