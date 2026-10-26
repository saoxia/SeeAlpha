"""Check batch loading preserves actual fills and marked-equity statistics."""

import json
import subprocess
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
checks = {}
for key in ("TFL", "TFS", "PBS", "MRL"):
    with (ROOT / f"{key}_streaming_parity.log").open("w") as log:
        for module, opts in [("research", ["--candidates", "baseline"]), ("streaming", [])]:
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    f"strategy_python._optimization.four_strategy_20260914.{module}",
                    key,
                    "parity",
                    *opts,
                ],
                stdout=log,
                stderr=subprocess.STDOUT,
                check=True,
            )
    a = ROOT / "runs" / key / "parity/baseline"
    b = ROOT / "stream_audit" / key / "parity/baseline"
    ma = json.loads((a / "metrics.json").read_text())
    mb = json.loads((b / "metrics.json").read_text())
    ma.pop("elapsed_seconds")
    mb.pop("elapsed_seconds")
    assert ma == mb, (key, ma, mb)
    fa = pd.read_csv(a / "fills.csv")
    fb = pd.read_csv(b / "fills.csv")
    assert len(fa) > 0, f"Need an active-trading parity sample: {key}"
    columns = ["side", "filled_qty", "avg_px", "ts_last", "commissions"]
    pd.testing.assert_frame_equal(fa[columns], fb[columns])
    checks[key] = {"metrics_equal": True, "fills_equal": True, "fills": len(fa)}
    print("PARITY", key, checks[key], flush=True)
(ROOT / "streaming_parity.json").write_text(json.dumps(checks, indent=2) + "\n")
