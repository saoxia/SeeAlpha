"""Execute four family parity checks and all fixed BTC evaluations with two workers."""

import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parents[2]
KEYS = list(json.loads((ROOT / "strategies.json").read_text()))


def execute(case):
    key, period, matched, mode = case
    name = f"{key}_{period}_{'matched' if matched else 'default'}_{mode}"
    command = [
        sys.executable,
        "-m",
        "strategy_python._research.btc_transfer_20260914.run",
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
    pilot = [
        (key, "pilot", False, mode)
        for key in KEYS
        if key.endswith("original")
        for mode in ("normal", "stream")
    ]
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(execute, pilot))
    parity = {}
    for key in KEYS:
        if not key.endswith("original"):
            continue
        base = ROOT / "runs/pilot" / key
        normal = json.loads((base / "normal/metrics.json").read_text())
        stream = json.loads((base / "stream/metrics.json").read_text())
        for field in ("net_pnl", "max_drawdown_pct", "trades", "turnover_usdt", "fees"):
            assert normal[field] == stream[field], (key, field)
        cols = ["side", "filled_qty", "avg_px", "commissions", "ts_last"]
        pd.testing.assert_frame_equal(
            pd.read_csv(base / "normal/fills.csv")[cols],
            pd.read_csv(base / "stream/fills.csv")[cols],
        )
        parity[key] = {"identical": True, "trades": stream["trades"]}
    (ROOT / "streaming_parity.json").write_text(json.dumps(parity, indent=2) + "\n")
    print("PARITY_PASSED", flush=True)
    cases = [(key, period, False, "stream") for key in KEYS for period in ("full", "recent")]
    cases += [("PBS_original", period, True, "stream") for period in ("full", "recent")]
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(execute, cases))
    print("ALL_18_COMPLETED", flush=True)


if __name__ == "__main__":
    main()
