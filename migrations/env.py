"""Alembic entry point; the connection URL is provided only through the environment."""

import os
import re

from alembic import context
from sqlalchemy import create_engine, inspect
from sqlalchemy.engine import make_url
from sqlalchemy.schema import CreateSchema

from api.config import Settings
from api.db.models import Base

config = context.config
url = config.attributes.get("database_url") or Settings.from_environment().database_url
if not url:
    raise RuntimeError("请通过 DATABASE_URL 指定待迁移数据库。")
parsed = make_url(url)
if parsed.get_backend_name() not in {"sqlite", "postgresql"}:
    raise ValueError("业务数据库只支持 SQLite 或 PostgreSQL。")
if parsed.get_backend_name() == "postgresql" and parsed.drivername == "postgresql":
    parsed = parsed.set(drivername="postgresql+psycopg")
schema = (
    (config.attributes.get("database_schema") or os.environ.get("DATABASE_SCHEMA") or "seealpha")
    if parsed.get_backend_name() == "postgresql"
    else None
)
if schema and not re.fullmatch(r"[a-z_][a-z0-9_]*", schema):
    raise ValueError("无效的数据库 schema 名称。")


def run_migrations_online():
    engine = create_engine(parsed)
    with engine.connect() as connection:
        if schema and not inspect(connection).has_schema(schema):
            connection.execute(CreateSchema(schema, if_not_exists=True))
        # Schema inspection starts a transaction too. End it before Alembic
        # starts its own transaction so the revision is committed on success.
        connection.commit()
        connection = connection.execution_options(schema_translate_map={None: schema})
        context.configure(
            connection=connection, target_metadata=Base.metadata, version_table_schema=schema
        )
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    raise RuntimeError("当前迁移需要实际数据库连接。")
else:
    run_migrations_online()
