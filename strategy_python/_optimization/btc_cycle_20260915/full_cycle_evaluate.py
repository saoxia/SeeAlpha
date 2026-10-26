"""Frozen finite full-history pool; invoke only after fourth_evaluate is terminal."""

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .evaluate_followups import execute

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parents[2]


def main():
    assert "FOURTH_COMPLETE" in (ROOT / "fourth_evaluate.log").read_text()
    cases = [
        ("next_run", s, "full", "next_base")
        for s in ["MRL_original", "MRL_optimized", "TFL_original", "TFS_original", "PBS_original"]
    ]
    cases += [
        ("next_run", s, "full", v)
        for s in ["MRL_original", "MRL_optimized"]
        for v in ["space_units1_same_unit", "space_units2_same_unit", "space_units2_same_risk"]
    ]
    cases += [
        ("third_run", "TFL_original", "full", "third_cost5_budget_half"),
        ("fourth_run", "TFL_original", "full", "fourth_clock30_budget_half"),
        ("next_run", "TFS_original", "full", "space_cap0.5"),
        ("third_run", "TFS_original", "full", "third_slope_lock80"),
        ("third_run", "PBS_original", "full", "third_risk_low_reward2"),
        ("third_run", "PBS_original", "full", "third_risk_low_reward2.5"),
    ]
    assert len(cases) == 17
    frozen = {}
    for name in [
        "prior_files_sha256.json",
        "frozen_runtime.json",
        "frozen_next_runtime.json",
        "frozen_third_runtime.json",
        "frozen_fourth_runtime.json",
    ]:
        for p, h in json.loads((ROOT / name).read_text()).items():
            assert hashlib.sha256((PROJECT / p).read_bytes()).hexdigest() == h, p
            if p in frozen:
                assert frozen[p] == h
            frozen[p] = h
    for name, value in [
        ("full_cycle_cases.json", [list(c) for c in cases]),
        ("frozen_full_cycle.json", frozen),
    ]:
        path = ROOT / name
        if path.exists():
            assert json.loads(path.read_text()) == value
        else:
            path.write_text(json.dumps(value, indent=2) + "\n")
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(execute, cases))
    for module, slot, period, variant in cases:
        p = ROOT / "runs" / period / (slot + "__" + variant)
        m = json.loads((p / "metrics.json").read_text())
        params = json.loads((p / "parameters.json").read_text())
        assert params["start"] == "2021-01-01T00:00:00+00:00"
        assert params["end"] == "2026-08-31T00:00:00+00:00"
        assert (
            m["off_tick_order_price_fields"] == 0 and abs(m["cashflow_reconciliation_error"]) < 1e-4
        )
        assert (
            params["runner_sha256"] == frozen[str((ROOT / (module + ".py")).relative_to(PROJECT))]
        )
    for p, h in frozen.items():
        assert hashlib.sha256((PROJECT / p).read_bytes()).hexdigest() == h, p
    print("FULL_CYCLE_COMPLETE", len(cases), flush=True)


if __name__ == "__main__":
    main()
