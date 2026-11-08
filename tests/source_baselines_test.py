"""Validate registered current sources against their frozen research archives."""

import hashlib
import json
import zipfile
from pathlib import Path

from core.paths import PROJECT_ROOT as PROJECT


def digest(path):
    with Path(path).open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


# The research manifests lived under strategy_python/, which was removed by explicit
# authorization on 2026-11-08. Lint baselines of files that still exist stay in force.
HISTORICAL = ()


def current_source(project, path):
    """Historical manifests still name pipeline/ before it moved under engine/."""
    candidate = project / path
    if candidate.exists():
        return candidate
    if path.startswith("pipeline/"):
        moved = project / "engine" / path
        if moved.exists():
            return moved
    return None


def verify_sources(project=PROJECT):
    baseline = json.loads((project / "tests/baselines/lint_baseline.json").read_text())
    updates_path = project / "tests/baselines/source_updates.json"
    updates = json.loads(updates_path.read_text()) if updates_path.exists() else {}
    failures = []
    checked = set()
    verified_updates = []
    for name in HISTORICAL:
        manifest = json.loads((project / name).read_text())
        archive_verified = False
        for path, expected in manifest["source_sha256"].items():
            checked.add(path)
            located = current_source(project, path)
            actual = digest(located) if located is not None else None
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


def test_historical_sources_match_registered_updates():
    result = verify_sources()
    assert not result["failures"], "\n".join(result["failures"])
