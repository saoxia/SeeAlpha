"""Fixed fourth-cycle compositions and budget neighbors; two native engines maximum."""

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .evaluate_followups import execute

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parents[2]


def main():
    experiment = json.loads((ROOT / "fourth_experiments.json").read_text())
    combinations = [
        (s, v) for s in ["TFL_original"] for v in experiment[s]
    ]
    smoke = [("fourth_run", s, "stress", v) for s, v in combinations]
    cases = [
        ("fourth_run", s, p, v) for s, v in combinations for p in ["develop", "validation", "review"]
    ]
    assert len(smoke) == 3 and len(cases) == 9
    frozen = json.loads((ROOT / "frozen_next_runtime.json").read_text())
    for filename in ["fourth_run.py", "fourth_experiments.json", "next_experiments.json"]:
        p = ROOT / filename
        frozen[str(p.relative_to(PROJECT))] = hashlib.sha256(p.read_bytes()).hexdigest()
    target = ROOT / "frozen_fourth_runtime.json"
    if target.exists():
        assert json.loads(target.read_text()) == frozen
    else:
        target.write_text(json.dumps(frozen, indent=2) + "\n")
    for name, work in [("fourth_smoke", smoke), ("fourth_cases", cases)]:
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
    print("FOURTH_COMPLETE", flush=True)


if __name__ == "__main__":
    main()
