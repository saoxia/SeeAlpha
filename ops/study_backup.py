"""Consistent SQLite snapshots plus independently verified public-response evidence."""

import argparse
import fcntl
import gzip
import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from forward_validation.core import verify

from .project_health import DEFAULT_ROOT, PROJECT, atomic_write, digest

BACKUPS = PROJECT / "data/forward_backups/btc_eth_mrl_20260921"


def backup_database(source, destination):
    with (
        closing(sqlite3.connect(f"file:{source}?mode=ro", uri=True)) as original,
        closing(sqlite3.connect(destination)) as copied,
    ):
        original.backup(copied, pages=256, sleep=0.01)
        if copied.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("Backup database integrity check failed")


def response_rows(db):
    return db.execute(
        "SELECT id,path,payload_sha256,chain_sha256 FROM requests ORDER BY id"
    ).fetchall()


def verify_chain(rows):
    chain = ""
    for _, _, payload, expected in rows:
        chain = hashlib.sha256((chain + payload).encode()).hexdigest()
        if chain != expected:
            raise RuntimeError("Public-response hash chain is broken")
    return chain


def archive_prefix(source, destination, expected):
    """Copy exactly the committed records; tolerate retained, uncommitted crash records."""
    orphaned = 0
    cursor = 0
    with gzip.open(source, "rb") as original, gzip.open(destination, "wb") as copied:
        while cursor < len(expected):
            line = original.readline()
            if not line:
                raise RuntimeError("Raw evidence ends before committed requests")
            actual = hashlib.sha256(line.rstrip(b"\n")).hexdigest()
            if actual != expected[cursor]:
                orphaned += 1
                continue
            copied.write(line)
            cursor += 1
    return orphaned


def validate_snapshot(directory):
    directory = Path(directory)
    record = json.loads((directory / "backup.json").read_text())
    for name, expected in record["files"].items():
        if digest(directory / name) != expected:
            raise RuntimeError("Backup file changed: " + name)
    with closing(
        sqlite3.connect(f"file:{directory / 'observations.sqlite'}?mode=ro", uri=True)
    ) as db:
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("Restored database failed integrity check")
        rows = response_rows(db)
        if verify_chain(rows) != record["request_chain"]:
            raise RuntimeError("Backup chain endpoint changed")
        groups = {}
        for _, path, payload, _ in rows:
            groups.setdefault(path, []).append(payload)
        for path, expected in groups.items():
            item = record["raw"][path]
            stored = directory.parent.parent / item["object"]
            if digest(stored) != item["sha256"]:
                raise RuntimeError("Backup raw object changed")
            with gzip.open(stored, "rb") as source:
                actual = [hashlib.sha256(line.rstrip(b"\n")).hexdigest() for line in source]
            if actual != expected:
                raise RuntimeError("Backup raw records disagree with SQLite")
    # Restore into a separate directory and reopen it, never overwrite the live study.
    with tempfile.TemporaryDirectory(prefix="restore-", dir=directory.parent) as scratch:
        restored = Path(scratch) / "restored.sqlite"
        backup_database(directory / "observations.sqlite", restored)
        with closing(sqlite3.connect(f"file:{restored}?mode=ro", uri=True)) as db:
            restored_requests = db.execute("SELECT COUNT(*) FROM requests").fetchone()[0]
        if restored_requests != len(rows):
            raise RuntimeError("Restore rehearsal lost requests")
    return {"verified": True, "requests": len(rows), "restore_rehearsal": "passed"}


def create(root=DEFAULT_ROOT, destination=BACKUPS):
    root, destination = Path(root), Path(destination)
    verify(root)
    destination.mkdir(parents=True, exist_ok=True)
    with (destination / "backup.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        identity = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        directory = destination / "snapshots" / identity
        directory.mkdir(parents=True)
        objects = destination / "objects"
        objects.mkdir(exist_ok=True)
        backup_database(root / "observations.sqlite", directory / "observations.sqlite")
        with closing(sqlite3.connect(directory / "observations.sqlite")) as db:
            rows = response_rows(db)
        chain = verify_chain(rows)
        groups = {}
        for identity_number, path, payload, _ in rows:
            groups.setdefault(path, []).append((identity_number, payload))
        raw = {}
        for path, expected in groups.items():
            source = (root / path).resolve()
            if not source.is_relative_to((root / "raw").resolve()):
                raise RuntimeError("Unexpected raw evidence path")
            last_id, last_hash = expected[-1]
            obj = objects / f"{last_id}-{last_hash}.jsonl.gz"
            orphans = 0
            if not obj.exists():
                temporary = obj.with_suffix(".tmp")
                orphans = archive_prefix(source, temporary, [r[1] for r in expected])
                with temporary.open("rb") as file:
                    os.fsync(file.fileno())
                temporary.replace(obj)
            raw[path] = {
                "object": str(obj.relative_to(destination)),
                "sha256": digest(obj),
                "records": len(expected),
                "uncommitted_records_skipped": orphans,
            }
        for name in ("manifest.json", "archive.json", "frozen_sources.zip"):
            shutil.copy2(root / name, directory / name)
        record = {
            "created_at": datetime.now(UTC).isoformat(),
            "source": str(root.resolve()),
            "request_chain": chain,
            "files": {p.name: digest(p) for p in directory.iterdir() if p.is_file()},
            "raw": raw,
        }
        atomic_write(directory / "backup.json", json.dumps(record, ensure_ascii=False, indent=2))
        verification = validate_snapshot(directory)
        atomic_write(directory / "verified.json", json.dumps(verification, indent=2))
        result = {
            "created_at": record["created_at"],
            "directory": str(directory),
            **verification,
            "limitation": "本机独立目录备份，可恢复逻辑损坏；不覆盖整盘或整机丢失",
        }
        atomic_write(destination / "latest.json", json.dumps(result, ensure_ascii=False, indent=2))
        # Retain seven verified snapshots; shared immutable raw objects remain while referenced.
        snapshots = sorted((destination / "snapshots").glob("*/verified.json"))
        retained = snapshots[-7:]
        for old in snapshots[:-7]:
            shutil.rmtree(old.parent)
        referenced = {
            item["object"]
            for marker in retained
            for item in json.loads((marker.parent / "backup.json").read_text())["raw"].values()
        }
        for obj in objects.glob("*.jsonl.gz"):
            if str(obj.relative_to(destination)) not in referenced:
                obj.unlink()
        return result


def main():
    parser = argparse.ArgumentParser(description="一致性备份并演练恢复，不覆盖研究原件")
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--destination", type=Path, default=BACKUPS)
    parser.add_argument("--verify", type=Path)
    args = parser.parse_args()
    result = validate_snapshot(args.verify) if args.verify else create(args.root, args.destination)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
