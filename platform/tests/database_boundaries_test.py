"""Engine-independent business database and deployment boundary regressions.

These tests do not create an Engine, Docker container or exchange connection.
They run in the standalone Platform dependency environment.
"""

import os
import sqlite3
import time
import uuid
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from api.config import Settings
from api.db.database import create_database
from api.db.errors import StorageUnavailable
from api.db.orm import ORMDatabase
from api.main import create_app
from sqlalchemy import create_engine, text


def test_orm_refuses_unmigrated_database(tmp_path):
    settings = Settings(tmp_path / "unused", database_url=f"sqlite:///{tmp_path / 'empty.db'}")
    db = create_database(settings)
    try:
        with pytest.raises(RuntimeError, match="迁移"):
            db.initialize()
    finally:
        db.close()
    assert not Path(tmp_path / "unused").exists()


def test_deployed_api_cannot_fall_back_to_inprocess_engine(tmp_path, monkeypatch):
    from api import config

    monkeypatch.setenv("ENGINE_URL", "inprocess")
    with pytest.raises(ValueError, match="HTTP"):
        config.Settings.from_environment()
    with pytest.raises(ValueError, match="不允许进程内 Engine"):
        create_app(Settings(
            tmp_path / "unused.db", database_url=f"sqlite:///{tmp_path / 'business.db'}",
            require_database_url=True, engine_url="inprocess",
        ))
    assert not (tmp_path / "business.db").exists()


def test_runtime_without_explicit_database_does_not_create_local_data(tmp_path, monkeypatch):
    from api import config
    from api.db.errors import StorageUnavailable

    monkeypatch.setattr(config, "PROJECT", tmp_path)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL_FILE", raising=False)
    monkeypatch.delenv("AUTH_DB_PATH", raising=False)
    settings = config.Settings.from_environment()
    database = create_database(settings)
    with pytest.raises(StorageUnavailable, match="配置 DATABASE_URL"):
        database.initialize()
    assert not settings.database_path.exists()


def test_fresh_sqlite_schema_rejects_preexisting_business_tables(tmp_path):
    url = f"sqlite:///{tmp_path / 'old.db'}"
    with sqlite3.connect(tmp_path / "old.db") as connection:
        connection.execute("CREATE TABLE old_business (value TEXT)")
        connection.execute("INSERT INTO old_business VALUES ('keep')")
    config = Config("alembic.ini")
    config.attributes["database_url"] = url
    with pytest.raises(RuntimeError, match="全新"):
        command.upgrade(config, "head")
    with sqlite3.connect(tmp_path / "old.db") as connection:
        assert connection.execute("SELECT value FROM old_business").fetchone()[0] == "keep"


def test_fresh_postgres_schema_rejects_preexisting_business_tables(tmp_path):
    url = os.environ.get("TEST_POSTGRES_URL")
    if not url:
        pytest.skip("TEST_POSTGRES_URL 未设置")
    schema = "test_" + uuid.uuid4().hex
    engine = create_engine(url.replace("postgresql://", "postgresql+psycopg://", 1))
    try:
        with engine.begin() as connection:
            from sqlalchemy.schema import CreateSchema

            connection.execute(CreateSchema(schema))
            connection.exec_driver_sql(f'CREATE TABLE "{schema}".old_business (value text)')
            connection.exec_driver_sql(
                f"""INSERT INTO "{schema}".old_business VALUES ('keep')"""
            )
        config = Config("alembic.ini")
        config.attributes.update(database_url=url, database_schema=schema)
        with pytest.raises(RuntimeError, match="全新"):
            command.upgrade(config, "head")
        with engine.begin() as connection:
            assert connection.scalar(
                text(f'SELECT value FROM "{schema}".old_business')
            ) == "keep"
    finally:
        with engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        engine.dispose()


def test_database_url_file_is_private(tmp_path, monkeypatch):
    from api.config import Settings

    connection_file = tmp_path / "database_url"
    connection_file.write_text(f"sqlite:///{tmp_path / 'orm.db'}\n")
    connection_file.chmod(0o600)
    monkeypatch.setenv("DATABASE_URL_FILE", str(connection_file))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    settings = Settings.from_environment()
    assert "orm.db" in settings.database_url
    assert settings.database_url not in repr(settings)
    assert create_database(settings).backend == "orm"
    connection_file.chmod(0o644)
    with pytest.raises(ValueError, match="权限"):
        Settings.from_environment()
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    assert Settings.from_environment().database_url == "sqlite:///:memory:"


def test_postgres_unresponsive_handshake_is_bounded_and_logs_no_credentials(tmp_path, caplog):
    import socket
    import threading

    stop = threading.Event()
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        listener.settimeout(10)

        def stalled_server():
            with listener.accept()[0]:
                stop.wait(10)

        server = threading.Thread(target=stalled_server, daemon=True)
        server.start()
        password = "private-diagnostic-password"
        db = ORMDatabase(
            f"postgresql+psycopg://fixture:{password}@127.0.0.1:{listener.getsockname()[1]}/fixture",
            tmp_path / "unused.db",
        )
        started = time.monotonic()
        try:
            with pytest.raises(StorageUnavailable), db.session() as session:
                session.execute(text("SELECT 1"))
            assert time.monotonic() - started < 9
            assert "error=OperationalError" in caplog.text
            assert password not in caplog.text
            assert "postgresql" not in caplog.text
        finally:
            stop.set()
            db.close()
            server.join(timeout=2)
