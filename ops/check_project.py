"""Repeatable project checks without dependency updates or research mutations."""

import argparse
import hashlib
import json
import subprocess
import sys
import zipfile
from datetime import UTC, datetime

from .project_health import PROJECT, atomic_write, digest

HISTORICAL = (
    "strategy_python/_optimization/btc_eth_signal_attribution_20260921/manifest.json",
    "strategy_python/_optimization/btc_long_validation_20260921/manifest.json",
)


def verify_sources(project=PROJECT):
    baseline = json.loads((project / "ops/lint_baseline.json").read_text())
    updates_path = project / "ops/source_updates.json"
    updates = json.loads(updates_path.read_text()) if updates_path.exists() else {}
    failures = []
    checked = set()
    verified_updates = []
    for name in HISTORICAL:
        manifest = json.loads((project / name).read_text())
        archive_verified = False
        for path, expected in manifest["source_sha256"].items():
            checked.add(path)
            actual = digest(project / path) if (project / path).exists() else None
            if actual == expected:
                continue
            update = updates.get(path, {})
            if (
                actual is not None
                and expected == update.get("previous_sha256")
                and actual == update.get("sha256")
            ):
                # 显式登记的共享管道更新允许继续开发；历史版本仍必须完整可恢复。
                root = (project / name).parent
                try:
                    if not archive_verified:
                        archive = json.loads((root / "archive.json").read_text())
                        if digest(root / "frozen_sources.zip") != archive["sha256"]:
                            raise ValueError("Historical archive hash mismatch")
                        archive_verified = True
                    with zipfile.ZipFile(root / "frozen_sources.zip") as source:
                        if hashlib.sha256(source.read(path)).hexdigest() != expected:
                            raise ValueError("Archived source hash mismatch")
                    verified_updates.append(f"{name}: {path}")
                    continue
                except (OSError, ValueError, KeyError, zipfile.BadZipFile):
                    pass
            failures.append(f"{name}: {path}")
    for path, item in baseline["files"].items():
        if not (project / path).exists() or digest(project / path) != item["sha256"]:
            failures.append(f"Style baseline changed; review the per-file exception: {path}")
    return {
        "historical_source_files": len(checked),
        "style_baseline_files": len(baseline["files"]),
        "verified_source_updates": verified_updates,
        "failures": failures,
    }


def main():
    parser = argparse.ArgumentParser(
        description="核验源码、运行代码检查和全量测试，不升级依赖或重跑历史收益"
    )
    parser.add_argument("--quick", action="store_true", help="只运行源码与代码检查")
    parser.add_argument(
        "--development", action="store_true", help="开发检查，不核验未迁移的前向观察运行状态"
    )
    args = parser.parse_args()
    identity = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    output = PROJECT / "data/project_checks" / identity
    output.mkdir(parents=True)
    sources = verify_sources()
    steps = [{"name": "historical_and_style_sources", "passed": not sources["failures"], **sources}]
    commands = [
        # Ruff only reads .gitignore inside Git checkouts; deployed copies must
        # exclude local dependencies and runtime output explicitly as well.
        (
            "lint",
            [
                sys.executable,
                "-m",
                "ruff",
                "check",
                "--extend-exclude",
                ".local,.venv,data,logs,reports,handover",
                ".",
            ],
        ),
    ]
    if not args.development:
        commands.insert(
            0, ("forward_freeze", [sys.executable, "-m", "forward_validation.service", "verify"])
        )
    if not args.quick:
        commands.append(("tests", [sys.executable, "-m", "pytest", "-q", "--disable-warnings"]))
    for name, command in commands:
        result = subprocess.run(command, cwd=PROJECT, capture_output=True, text=True, check=False)
        log = result.stdout + result.stderr
        atomic_write(output / f"{name}.log", log)
        steps.append(
            {
                "name": name,
                "passed": result.returncode == 0,
                "returncode": result.returncode,
                "log": str(output / f"{name}.log"),
                "summary": log.strip().splitlines()[-1] if log.strip() else "",
            }
        )
        print(f"{name}: {'PASS' if result.returncode == 0 else 'FAIL'}", flush=True)
    report = {
        "checked_at": datetime.now(UTC).isoformat(),
        "passed": all(s["passed"] for s in steps),
        "full_tests": not args.quick,
        "development": args.development,
        "steps": steps,
        "artifact_directory": str(output),
    }
    text = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    atomic_write(output / "results.json", text)
    atomic_write(output.parent / "latest.json", text)
    print(text)
    return int(not report["passed"])


if __name__ == "__main__":
    raise SystemExit(main())
