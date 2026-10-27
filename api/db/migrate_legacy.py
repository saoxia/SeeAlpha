"""Explicit one-time SQLite backup; preserves legacy data and session compatibility."""

import argparse
import sqlite3
from contextlib import closing
from pathlib import Path

from ..config import Settings
from .database import Database


def migrate(source: Path, destination: Path):
    if not source.is_file():
        raise ValueError("来源数据库不存在。")
    if destination.exists():
        raise ValueError("目标数据库已存在，拒绝覆盖。")
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Exclusive creation also protects against accidentally replacing a concurrent migration.
    with destination.open("xb"):
        destination.chmod(0o600)
    try:
        with (
            closing(sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)) as old,
            closing(sqlite3.connect(destination)) as new,
        ):
            old.backup(new)
            if new.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("数据库完整性检查失败。")
        Database(destination).initialize()
    except Exception:
        destination.unlink(missing_ok=True)
        raise


def main():
    parser = argparse.ArgumentParser(description="迁移本地旧版 Web 数据库；不覆盖已有目标")
    parser.add_argument("source", type=Path)
    args = parser.parse_args()
    try:
        migrate(args.source, Settings.from_environment().database_path)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    print("SQLite 数据库已复制并初始化；原文件保留。导入 ORM 业务数据库请使用 api.db.import_sqlite。")


if __name__ == "__main__":
    main()
