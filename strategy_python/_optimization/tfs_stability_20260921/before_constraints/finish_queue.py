"""Two extra workers stage the queue tail; atomically publish only unclaimed runs.

Only the output directory is redirected. Manifest bytes, strategy factory, data,
parameters and frozen study source are identical. An already claimed primary run
is never overwritten, even if another worker finishes the same experiment first.
"""

import argparse
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

from . import study

ROOT = study.ROOT
STAGING = ROOT / "staging"


def stage_one(experiment_id):
    study.ROOT = STAGING
    output = study.run(experiment_id)
    destination = ROOT / "runs" / experiment_id
    try:
        output.rename(destination)
        print("PUBLISHED", experiment_id, flush=True)
    except OSError:
        if not destination.exists():
            raise
        print("PRIMARY ALREADY CLAIMED", experiment_id, flush=True)


def launch(row):
    if (ROOT / "runs" / row["id"]).exists():
        return
    env = dict(os.environ, OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1", MKL_NUM_THREADS="1")
    with (STAGING / (row["id"] + ".log")).open("w") as stream:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "strategy_python._optimization.tfs_stability_20260921.finish_queue",
                "--one",
                row["id"],
            ],
            stdout=stream,
            stderr=subprocess.STDOUT,
            env=env,
            check=False,
        )
    if result.returncode:
        raise RuntimeError("Staged execution failed: " + row["id"])
    print("STAGED", row["id"], flush=True)


def main():
    manifest = json.loads((ROOT / "manifest.json").read_text())
    study.verify(manifest, data=True)
    STAGING.mkdir(exist_ok=True)
    (STAGING / "manifest.json").write_bytes((ROOT / "manifest.json").read_bytes())
    rows = sorted(
        (r for r in manifest["experiments"] if r["kind"] == "grid"),
        key=lambda r: r["id"],
        reverse=True,
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(launch, rows))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--one")
    args = parser.parse_args()
    if args.one:
        stage_one(args.one)
    else:
        main()
