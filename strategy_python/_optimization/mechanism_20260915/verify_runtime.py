"""Verify actual runs and continuous-batch equivalence, not only unit-test status."""

import hashlib
import json
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parents[2]


def expected_sources(info):
    paths = list((PROJECT / "strategy_python" / info["package"]).glob("*.py"))
    if info["kind"] == "mechanism":
        paths += [
            p
            for p in (PROJECT / "strategy_python/_mechanism").iterdir()
            if p.suffix in (".py", ".json")
        ]
    return {str(p.relative_to(PROJECT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


def main():
    registry = json.loads((ROOT / "runtime_strategies.json").read_text())
    cases = json.loads((ROOT / "stress_cases.json").read_text())
    results = []
    for key, period, variant, mode in cases:
        directory = ROOT / "runs" / period / key / mode
        params = json.loads((directory / "parameters.json").read_text())
        metric = json.loads((directory / "metrics.json").read_text())
        assert params["source_sha256"] == expected_sources(registry[key]), key
        assert params["runner_sha256"] == hashlib.sha256((ROOT / "run.py").read_bytes()).hexdigest()
        assert metric["trades"] > 0, key
        assert metric["off_tick_order_price_fields"] == 0
        assert abs(metric["cashflow_reconciliation_error"]) < 0.0001
        results.append({"key": key, "mode": mode, "trades": metric["trades"]})
    for slot in ("MRL_original", "TFL", "TFS", "PBS"):
        directory = ROOT / "runs/stress" / ("BTC_" + slot)
        cols = ["side", "filled_qty", "avg_px", "commissions", "ts_last"]
        pd.testing.assert_frame_equal(
            pd.read_csv(directory / "stream/fills.csv")[cols],
            pd.read_csv(directory / "normal/fills.csv")[cols],
        )
        left, right = [
            json.loads((directory / mode / "metrics.json").read_text())
            for mode in ("stream", "normal")
        ]
        for name in ("net_pnl", "fees", "trades", "max_drawdown_pct"):
            assert left[name] == right[name], (slot, name)
    sources = {}
    for info in registry.values():
        sources.update(expected_sources(info))
    (ROOT / "frozen_runtime_sources.json").write_text(json.dumps(sources, indent=2) + "\n")
    (ROOT / "runtime_verification.json").write_text(
        json.dumps(
            {
                "stress_runs": results,
                "family_streaming_parity": 4,
                "all_prices_on_tick": True,
                "all_cashflows_reconciled": True,
            },
            indent=2,
        )
        + "\n"
    )
    print("RUNTIME_VERIFIED", len(results))


if __name__ == "__main__":
    main()
