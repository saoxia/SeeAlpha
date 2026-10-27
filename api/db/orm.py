"""SQLAlchemy sessions shared by portable business repositories."""

import re
from contextlib import contextmanager
from pathlib import Path

from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError, DBAPIError, IntegrityError
from sqlalchemy.orm import sessionmaker

from .errors import StorageConflict, StorageUnavailable
from .models import Base


class ORMDatabase:
    backend = "orm"

    def __init__(self, url, path: Path, *, schema=None):
        try:
            parsed = make_url(url)
        except ArgumentError:
            raise ValueError("数据库连接格式无效。") from None
        if parsed.get_backend_name() not in {"sqlite", "postgresql"}:
            raise ValueError("业务数据库只支持 SQLite 或 PostgreSQL。")
        if parsed.get_backend_name() == "postgresql" and parsed.drivername == "postgresql":
            parsed = parsed.set(drivername="postgresql+psycopg")
        self.path = path
        self.schema = schema or ("seealpha" if parsed.get_backend_name() == "postgresql" else None)
        if self.schema and not re.fullmatch(r"[a-z_][a-z0-9_]*", self.schema):
            raise ValueError("无效的数据库 schema 名称。")
        options = {"pool_pre_ping": True}
        if parsed.get_backend_name() == "sqlite":
            options["connect_args"] = {"timeout": 10, "check_same_thread": False}
        else:
            options.update(pool_size=4, max_overflow=0)
        self.engine = create_engine(parsed, **options)
        if parsed.get_backend_name() == "sqlite":

            @event.listens_for(self.engine, "connect")
            def sqlite_setup(connection, _record):
                connection.execute("PRAGMA foreign_keys = ON")

        self.engine = self.engine.execution_options(schema_translate_map={None: self.schema})
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)

    def initialize(self):
        if self.engine.dialect.name == "sqlite":
            database = self.engine.url.database
            if database and database != ":memory:":
                target = Path(database)
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                target.touch(mode=0o600, exist_ok=True)
                target.chmod(0o600)
        # Production schemas must be migrated explicitly. Never create or silently
        # replace existing cloud data at application startup.
        try:
            inspector = inspect(self.engine)
            missing = [
                name
                for name in Base.metadata.tables
                if not inspector.has_table(name, schema=self.schema)
            ]
            with self.engine.connect() as connection:
                revision = MigrationContext.configure(
                    connection, opts={"version_table_schema": self.schema}
                ).get_current_revision()
            alembic_file = Path(__file__).resolve().parents[2] / "alembic.ini"
            head = ScriptDirectory.from_config(Config(str(alembic_file))).get_current_head()
            if missing or revision != head:
                raise StorageUnavailable("业务数据库结构未就绪，请先执行 Alembic 迁移。")
        except DBAPIError:
            raise StorageUnavailable("业务数据库连接失败，请检查配置和数据库状态。") from None

    @contextmanager
    def session(self, *, write=False, lock_key=None):
        with self.sessions() as session:
            try:
                if write and self.engine.dialect.name == "sqlite":
                    session.connection().exec_driver_sql("BEGIN IMMEDIATE")
                elif write and lock_key is not None and self.engine.dialect.name == "postgresql":
                    session.execute(
                        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                        {"key": lock_key},
                    )
                yield session
                session.commit()
            except IntegrityError:
                session.rollback()
                raise StorageConflict("业务记录冲突") from None
            except DBAPIError:
                session.rollback()
                raise StorageUnavailable("业务数据库暂不可用，请稍后重试。") from None
            except Exception:
                session.rollback()
                raise

    def close(self):
        self.engine.dispose()
