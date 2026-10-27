"""Import a consistent legacy SQLite backup into an empty ORM database."""

import argparse
import json
import os
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import func, select, text

from api.config import Settings
from api.db.database import create_database
from api.db.models import (
    BacktestReport,
    ExchangeAccount,
    LedgerAlias,
    LedgerObservation,
    LedgerOperation,
    LedgerSync,
    LoginFailure,
    ManagementJob,
    OlapState,
    Session,
    User,
)

_TABLES = (
    ("users", User),
    ("sessions", Session),
    ("login_failures", LoginFailure),
    ("exchange_accounts", ExchangeAccount),
    ("ledger_operations", LedgerOperation),
    ("ledger_observations", LedgerObservation),
    ("ledger_aliases", LedgerAlias),
    ("ledger_sync", LedgerSync),
)
_OLAP = (
    ("settings", "olap_settings"),
    ("monitor", "clickhouse_monitor_settings"),
    ("cache", "clickhouse_monitor_cache"),
)


def import_legacy(source: Path, database, *, backup_directory: Path):
    if database.backend != "orm":
        raise ValueError("请先配置目标 DATABASE_URL。")
    if not source.is_file():
        raise ValueError("来源 SQLite 数据库不存在。")
    if (
        database.engine.dialect.name == "sqlite"
        and database.engine.url.database
        and Path(database.engine.url.database).resolve() == source.resolve()
    ):
        raise ValueError("来源和目标不能是同一个数据库。")
    backup_directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    backup = backup_directory / f"before-orm-import-{timestamp}.db"
    descriptor = os.open(backup, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(descriptor)
    with (
        closing(sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)) as original,
        closing(sqlite3.connect(backup)) as snapshot,
    ):
        original.backup(snapshot)
        if snapshot.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("来源数据库完整性检查失败，备份已保留。")
    counts = {}
    with (
        closing(sqlite3.connect(backup)) as snapshot,
        database.session(write=True, lock_key="orm-import") as session,
    ):
        snapshot.row_factory = sqlite3.Row
        if database.engine.dialect.name == "postgresql":
            # Fixed names come from ORM metadata; lock prevents concurrent writers
            # from invalidating the empty-target check during this transaction.
            for model in [item[1] for item in _TABLES] + [OlapState]:
                table = database.engine.dialect.identifier_preparer.format_table(model.__table__)
                schema = database.engine.dialect.identifier_preparer.quote_schema(database.schema)
                session.execute(text(f"LOCK TABLE {schema}.{table} IN EXCLUSIVE MODE"))
        if any(
            session.scalar(select(func.count()).select_from(model)) for _, model in _TABLES
        ) or any(
            session.scalar(select(func.count()).select_from(model))
            for model in (OlapState, ManagementJob, BacktestReport)
        ):
            raise ValueError("目标数据库已有记录，拒绝导入；来源备份已保留。")
        available = {
            row[0] for row in snapshot.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        for table_name, model in _TABLES:
            counts[table_name] = 0
            if table_name not in available:
                continue
            allowed = {column.name for column in model.__table__.columns}
            cursor = snapshot.execute(f'SELECT * FROM "{table_name}"')
            while rows := cursor.fetchmany(500):
                for original_row in rows:
                    value = {k: original_row[k] for k in list(original_row.keys()) if k in allowed}
                    if "body" in value:
                        value["body"] = json.loads(value["body"])
                    session.add(model(**value))
                    counts[table_name] += 1
                session.flush()
                session.expunge_all()
        counts["olap_state"] = 0
        for name, table_name in _OLAP:
            if table_name in available:
                item = snapshot.execute(f'SELECT * FROM "{table_name}" WHERE id=1').fetchone()
                if item:
                    session.add(
                        OlapState(
                            name=name, value={k: item[k] for k in list(item.keys()) if k != "id"}
                        )
                    )
                    counts["olap_state"] += 1
        session.flush()
        for table_name, model in _TABLES:
            if session.scalar(select(func.count()).select_from(model)) != counts[table_name]:
                raise ValueError("迁移记录数量校验失败，事务已回滚。")
        if session.scalar(select(func.count()).select_from(OlapState)) != counts["olap_state"]:
            raise ValueError("OLAP 设置校验失败，事务已回滚。")
        if database.engine.dialect.name == "postgresql":
            for name, model in (
                ("users", User),
                ("login_failures", LoginFailure),
                ("exchange_accounts", ExchangeAccount),
            ):
                sequence = session.scalar(
                    text("SELECT pg_get_serial_sequence(:table_name, 'id')"),
                    {"table_name": f"{database.schema}.{name}"},
                )
                maximum = session.scalar(select(func.max(model.id)))
                if sequence and maximum is not None:
                    session.execute(
                        text("SELECT setval(CAST(:sequence AS regclass), :maximum, true)"),
                        {"sequence": sequence, "maximum": maximum},
                    )
    return {"backup": str(backup), "counts": counts}


def main():
    parser = argparse.ArgumentParser(description="将本地 SQLite 业务数据导入空的 ORM 业务库")
    parser.add_argument("source", type=Path)
    args = parser.parse_args()
    settings = Settings.from_environment()
    database = create_database(settings)
    try:
        database.initialize()
        result = import_legacy(
            args.source, database, backup_directory=settings.database_path.parent / "backups"
        )
    finally:
        database.close()
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
