"""Fixed third-cycle compositions and budget neighbors; two native engines maximum."""

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .evaluate_followups import execute

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parents[2]


def main():
    experiment = json.loads((ROOT / "third_experiments.json").read_text())
    combinations = [
        (s, v) for s in ["TFL_original", "TFS_original", "PBS_original"] for v in experiment[s]
    ]
    smoke = [("third_run", s, "stress", v) for s, v in combinations]
    cases = [
        ("third_run", s, p, v) for s, v in combinations for p in ["develop", "validation", "review"]
    ]
    cases += [
        ("next_run", s, p, "space_units2_same_unit")
        for s in ["MRL_original", "MRL_optimized"]
        for p in ["validation", "review"]
    ]
    assert len(smoke) == 6 and len(cases) == 22
    frozen = json.loads((ROOT / "frozen_next_runtime.json").read_text())
    for filename in ["third_run.py", "third_experiments.json", "next_experiments.json"]:
        p = ROOT / filename
        frozen[str(p.relative_to(PROJECT))] = hashlib.sha256(p.read_bytes()).hexdigest()
    target = ROOT / "frozen_third_runtime.json"
    if target.exists():
        assert json.loads(target.read_text()) == frozen
    else:
        target.write_text(json.dumps(frozen, indent=2) + "\n")
    for name, work in [("third_smoke", smoke), ("third_cases", cases)]:
        path = ROOT / (name + ".json")
        if path.exists():
            assert json.loads(path.read_text()) == [list(c) for c in work]
        else:
            path.write_text(json.dumps(work, indent=2) + "\n")
        print("STAGE_START", name, len(work), flush=True)
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(execute, work))
        for module, slot, period, variant in work:
            p = ROOT / "runs" / period / (slot + "__" + variant)
            if period == "stress":
                p = p / "stream"
            metrics = json.loads((p / "metrics.json").read_text())
            params = json.loads((p / "parameters.json").read_text())
            assert metrics["off_tick_order_price_fields"] == 0
            assert abs(metrics["cashflow_reconciliation_error"]) < 1e-4
            assert (
                params["runner_sha256"]
                == frozen[str((ROOT / (module + ".py")).relative_to(PROJECT))]
            )
            for source, digest in params["source_sha256"].items():
                assert hashlib.sha256((PROJECT / source).read_bytes()).hexdigest() == digest, source
        for source, digest in frozen.items():
            assert hashlib.sha256((PROJECT / source).read_bytes()).hexdigest() == digest, source
        print("STAGE_DONE", name, flush=True)
    print("THIRD_COMPLETE", flush=True)


if __name__ == "__main__":
    main()
