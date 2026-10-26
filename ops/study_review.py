"""Offline, restartable deadline guard around the unchanged frozen evaluator."""

import argparse
import fcntl
import json
import shutil
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

from forward_validation import review
from forward_validation.core import verify

from .project_health import DEFAULT_ROOT, atomic_write, digest
from .study_backup import backup_database


def valid_result(result, manifest):
    if result.get("live_trading_authorized") is not False:
        return False
    try:
        completed = datetime.fromisoformat(result["completed_at"]).timestamp() * 1000
        if completed < manifest["review_ms"]:
            return False
        decision = review.decide(result["summary"], result["counts"])
        return all(result.get(key) == value for key, value in decision.items())
    except (KeyError, TypeError, ValueError):
        return False


def finalize(root=DEFAULT_ROOT, *, stop_service=True):
    root = Path(root)
    manifest = verify(root)
    now = int(time.time() * 1000)
    if now < manifest["review_ms"]:
        return {"state": "pending", "due_ms": manifest["review_ms"], "evaluated": False}
    with (root / "review_guard.lock").open("a+") as guard:
        fcntl.flock(guard, fcntl.LOCK_EX | fcntl.LOCK_NB)
        marker = root / "review_completion.json"
        if marker.exists():
            result = json.loads(marker.read_text())
            for path, expected in result["files"].items():
                if digest(root / path) != expected:
                    raise RuntimeError("Completed assessment changed: " + path)
            return result
        # A stopped/offline collector must not prevent the fixed-date assessment.
        if stop_service:
            subprocess.run(
                ["systemctl", "--user", "stop", "btc-eth-mrl-forward.service"],
                check=True,
                timeout=65,
            )
        with (root / "collector.lock").open("a+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            frozen = root / "final_snapshot"
            if not frozen.exists():
                staging = root / "final_snapshot.pending"
                staging.mkdir(exist_ok=True)
                backup_database(root / "observations.sqlite", staging / "observations.sqlite")
                for name in ("manifest.json", "archive.json", "frozen_sources.zip"):
                    shutil.copy2(root / name, staging / name)
                atomic_write(
                    staging / "input.json",
                    json.dumps(
                        {
                            "captured_at": datetime.now(UTC).isoformat(),
                            "database_sha256": digest(staging / "observations.sqlite"),
                            "manifest_sha256": digest(staging / "manifest.json"),
                        },
                        indent=2,
                    ),
                )
                staging.rename(frozen)
            provenance = json.loads((frozen / "input.json").read_text())
            if digest(frozen / "observations.sqlite") != provenance["database_sha256"]:
                raise RuntimeError("Final input snapshot changed")
            # Frozen evaluate opens a writable DB; run on a disposable copy so retries
            # cannot change the final evidence snapshot or original study database.
            work = root / "review_runs" / datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
            work.mkdir(parents=True)
            for name in (
                "observations.sqlite",
                "manifest.json",
                "archive.json",
                "frozen_sources.zip",
            ):
                shutil.copy2(frozen / name, work / name)
            result = review.evaluate(work)
            if not valid_result(result, manifest):
                raise RuntimeError("Frozen evaluation returned an invalid or early result")
            old = root / "review.json"
            if old.exists():
                previous = json.loads(old.read_text())
                if previous.get("completed_at") and not (root / "collector_review.json").exists():
                    shutil.copy2(old, root / "collector_review.json")
                    shutil.copy2(root / "review.html", root / "collector_review.html")
            shutil.copytree(work / "assessment", root / "assessment", dirs_exist_ok=True)
            for name in ("review.json", "review.html"):
                atomic_write(root / name, (work / name).read_text())
            # Mark completion only after the whole report is published.
            paths = [
                root / "review.json",
                root / "review.html",
                *sorted((root / "assessment").iterdir()),
            ]
            completion = {
                "state": "completed",
                "completed_at": datetime.now(UTC).isoformat(),
                "due_ms": manifest["review_ms"],
                "decision": result["decision"],
                "live_trading_authorized": False,
                "input": provenance,
                "files": {str(p.relative_to(root)): digest(p) for p in paths if p.is_file()},
            }
            atomic_write(marker, json.dumps(completion, ensure_ascii=False, indent=2))
            return completion


def status(root=DEFAULT_ROOT):
    root = Path(root)
    manifest = verify(root)
    now = int(time.time() * 1000)
    return {
        "state": "completed"
        if (root / "review_completion.json").exists()
        else "due"
        if now >= manifest["review_ms"]
        else "pending",
        "due_ms": manifest["review_ms"],
    }


def main():
    parser = argparse.ArgumentParser(description="到期自动评估；未到期不读取收益或停止采集")
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--status", action="store_true")
    args = parser.parse_args()
    print(
        json.dumps(
            status(args.root) if args.status else finalize(args.root), ensure_ascii=False, indent=2
        )
    )


if __name__ == "__main__":
    main()
