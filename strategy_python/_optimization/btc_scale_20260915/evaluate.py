"""Run all eight BTC-scale strategies through a month of native-engine execution."""

import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parents[2]
KEYS = list(json.loads((ROOT / "strategies.json").read_text()))


def execute(case):
    key, period, matched, mode = case
    name = f"{key}_{period}_{'matched' if matched else 'default'}_{mode}"
    command = [
        sys.executable,
        "-m",
        "strategy_python._optimization.btc_scale_20260915.run",
        key,
        period,
        "--mode",
        mode,
    ]
    if matched:
        command.append("--matched")
    if period == "pilot":
        command += ["--batch-days", "7"]
    print("START", name, flush=True)
    with (ROOT / f"{name}.log").open("w") as log:
        subprocess.run(command, cwd=PROJECT, stdout=log, stderr=subprocess.STDOUT, check=True)
    print("DONE", name, flush=True)


def main():
    cases = [(key, "pilot", False, "stream") for key in KEYS]
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(execute, cases))
    summary = {}
    for key in KEYS:
        metrics = json.loads((ROOT / "runs/pilot" / key / "stream/metrics.json").read_text())
        assert metrics["trades"] > 0, key
        assert abs(metrics["cashflow_reconciliation_error"]) < .0001
        summary[key] = {name: metrics[name] for name in ("trades", "net_pnl", "max_drawdown_pct", "cashflow_reconciliation_error")}
    (ROOT / "smoke_results.json").write_text(json.dumps(summary, indent=2) + "\n")
    print("ALL_8_SMOKE_PASSED", flush=True)


if __name__ == "__main__":
    main()
