"""Behavioral contracts for the portable business database."""

import os
import sqlite3
import uuid
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from api.auth.security import hash_password
from api.config import Settings
from api.db.auth_repository import auth_repository
from api.db.database import create_database
from api.db.errors import StorageConflict
from api.db.exchange_repository import exchange_repository
from api.db.orm import ORMDatabase
from api.main import create_app
from api.olap.repository import olap_repository
from api.operations.orm_repository import ORMOperations


@pytest.fixture(params=["sqlite", "postgresql"])
def orm(tmp_path, request):
    if request.param == "postgresql":
        url = os.environ.get("TEST_POSTGRES_URL")
        if not url:
            pytest.skip("TEST_POSTGRES_URL 未设置")
        schema = "test_" + uuid.uuid4().hex
    else:
        url = f"sqlite:///{tmp_path / 'orm.db'}"
        schema = None
    config = Config("alembic.ini")
    config.attributes.update(database_url=url, database_schema=schema)
    command.upgrade(config, "head")
    database = ORMDatabase(url, tmp_path / "key-reference.db", schema=schema)
    database.initialize()
    yield database, url, schema
    database.close()
    if schema:
        engine = create_engine(url.replace("postgresql://", "postgresql+psycopg://", 1))
        with engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        engine.dispose()


def test_orm_auth_accounts_and_olap_persist(orm):
    db, _, _ = orm
    auth = auth_repository(db)
    user = auth.create_user("Admin", "hash")
    assert auth.get_user_by_username("admin")["id"] == user["id"]
    with pytest.raises(StorageConflict):
        auth.create_user("ADMIN", "another")
    auth.create_session(user["id"], "session-hash", 4_000_000_000)
    assert auth.get_user_for_session("session-hash")["username"] == "Admin"
    auth.reset_password(user["id"], "new-hash")
    assert auth.get_user_for_session("session-hash") is None
    accounts = exchange_repository(db)
    account = accounts.create_exchange_account(
        exchange="binance",
        account_name="Main",
        environment="production",
        credentials_encrypted="ciphertext",
        market_symbols_json="[]",
    )
    assert accounts.get_exchange_account(account["id"])["credentials_encrypted"] == "ciphertext"
    with pytest.raises(StorageConflict):
        accounts.create_exchange_account(
            exchange="binance",
            account_name="main",
            environment="production",
            credentials_encrypted="other",
            market_symbols_json="[]",
        )
    olap = olap_repository(db)
    olap.save_olap_settings(
        address="localhost",
        port=8123,
        database_name="test",
        username="u",
        password_encrypted="ciphertext",
    )
    olap.save_clickhouse_monitor_settings(refresh_interval_seconds=60, tables_json='["bars"]')
    assert olap.get_olap_settings()["password_encrypted"] == "ciphertext"
    assert olap.get_clickhouse_monitor_settings()["tables_json"] == '["bars"]'


def test_orm_api_restart_preserves_state(orm, tmp_path):
    db, url, schema = orm
    auth_repository(db).create_user("admin", hash_password("test-password-123"))
    settings = Settings(
        tmp_path / "reference.db",
        database_url=url,
        database_schema=schema or "",
        cookie_secure=False,
    )
    with TestClient(create_app(settings)) as client:
        assert (
            client.post(
                "/api/auth/login", json={"username": "admin", "password": "test-password-123"}
            ).status_code
            == 200
        )
        assert client.get("/api/auth/me").json()["user"]["username"] == "admin"
        cookie = client.cookies.get("seealpha_session")
    with TestClient(create_app(settings)) as client:
        client.cookies.set("seealpha_session", cookie)
        assert client.get("/api/auth/me").status_code == 200
        assert client.get("/api/system/exchange-accounts").status_code == 200


def test_orm_ledger_idempotence_and_merge(orm):
    from tests.api.operations_test import event

    db, _, _ = orm
    repo = ORMOperations(db)
    first = repo.ingest(event())
    assert repo.ingest(event())["observation_added"] is False
    venue = repo.ingest(
        event(
            event_id="venue-only",
            client_order_id=None,
            order_id="42",
            evidence="venue",
            strategy_id=None,
            price="2001",
        )
    )
    repo.ingest(event(event_id="accepted", order_id="42"))
    assert len(repo.list()["records"]) == 1
    assert repo.detail(venue["operation_id"])["operation"]["id"] == first["operation_id"]
    assert repo.detail(first["operation_id"])["operation"]["price"] == "2001"


def test_orm_refuses_unmigrated_database(tmp_path):
    settings = Settings(tmp_path / "unused", database_url=f"sqlite:///{tmp_path / 'empty.db'}")
    db = create_database(settings)
    try:
        with pytest.raises(RuntimeError, match="迁移"):
            db.initialize()
    finally:
        db.close()
    assert not Path(tmp_path / "unused").exists()


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


def test_import_existing_sqlite_preserves_records_and_rejects_nonempty_target(orm, tmp_path):
    from api.db.auth_repository import AuthRepository
    from api.db.database import Database
    from api.db.exchange_repository import ExchangeRepository
    from api.db.import_sqlite import import_legacy
    from api.olap.repository import OlapRepository
    from api.operations.repository import Operations
    from tests.api.operations_test import event

    target, _, _ = orm
    source = Database(tmp_path / "legacy.db")
    source.initialize()
    auth = AuthRepository(source)
    user = auth.create_user("admin", "legacy-hash")
    auth.create_session(user["id"], "legacy-session", 4_000_000_000)
    ExchangeRepository(source).create_exchange_account(
        exchange="binance",
        account_name="original",
        environment="production",
        credentials_encrypted="ciphertext",
        market_symbols_json="[]",
    )
    olap = OlapRepository(source)
    olap.initialize()
    olap.save_olap_settings(
        address="localhost",
        port=8123,
        database_name="test",
        username="u",
        password_encrypted="encrypted",
    )
    ledger = Operations(source)
    ledger.initialize()
    operation = ledger.ingest(event())
    result = import_legacy(source.path, target, backup_directory=tmp_path / "backup")
    assert Path(result["backup"]).exists()
    assert auth_repository(target).get_user_by_username("admin")["password_hash"] == "legacy-hash"
    assert auth_repository(target).get_user_for_session("legacy-session")["username"] == "admin"
    assert (
        exchange_repository(target).list_exchange_accounts()[0]["credentials_encrypted"]
        == "ciphertext"
    )
    assert olap_repository(target).get_olap_settings()["password_encrypted"] == "encrypted"
    assert (
        ORMOperations(target).detail(operation["operation_id"])["operation"]["id"]
        == operation["operation_id"]
    )
    next_user = auth_repository(target).create_user("other", "hash")
    assert next_user["id"] > user["id"]
    with pytest.raises(ValueError, match="目标数据库已有记录"):
        import_legacy(source.path, target, backup_directory=tmp_path / "backup")
    assert source.path.exists()


def test_postgres_existing_schema_is_verified_and_preserved(tmp_path):
    url = os.environ.get("TEST_POSTGRES_URL")
    if not url:
        pytest.skip("TEST_POSTGRES_URL 未设置")
    schema = "test_" + uuid.uuid4().hex
    engine = create_engine(url.replace("postgresql://", "postgresql+psycopg://", 1))
    try:
        with engine.begin() as connection:
            from sqlalchemy.schema import CreateSchema

            from api.db.models import Base
            from api.db.schema_compat import _BASELINE_TABLES

            connection.execute(CreateSchema(schema))
            mapped = connection.execution_options(schema_translate_map={None: schema})
            Base.metadata.create_all(
                mapped, tables=[Base.metadata.tables[name] for name in _BASELINE_TABLES]
            )
            connection.exec_driver_sql(
                f'CREATE TABLE "{schema}".schema_versions (version integer primary key)'
            )
            connection.execute(
                text(
                    f'INSERT INTO "{schema}".users (username,password_hash,created_at) VALUES (:username,:password,1)'
                ),
                {"username": "legacy-user", "password": "legacy-hash"},
            )
            connection.exec_driver_sql(f'DROP INDEX "{schema}".orm_users_username_unique')
        config = Config("alembic.ini")
        config.attributes.update(database_url=url, database_schema=schema)
        with pytest.raises(RuntimeError, match="唯一索引"):
            command.upgrade(config, "head")
        with engine.begin() as connection:
            assert connection.scalar(text(f'SELECT count(*) FROM "{schema}".users')) == 1
            connection.exec_driver_sql(
                f'CREATE UNIQUE INDEX orm_users_username_unique ON "{schema}".users (lower(username))'
            )
        command.upgrade(config, "head")
        db = ORMDatabase(url, tmp_path / "unused.db", schema=schema)
        db.initialize()
        assert (
            auth_repository(db).get_user_by_username("LEGACY-USER")["password_hash"]
            == "legacy-hash"
        )
        auth_repository(db).create_user("another-user", "hash")
        db.close()
    finally:
        with engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        engine.dispose()


def test_failed_import_rolls_back_all_rows(orm, tmp_path):
    from api.db.import_sqlite import import_legacy
    from api.db.models import User

    target, _, _ = orm
    source = tmp_path / "broken.db"
    with sqlite3.connect(source) as connection:
        connection.execute(
            "CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT, password_hash TEXT, created_at INTEGER)"
        )
        connection.executemany(
            "INSERT INTO users VALUES (?, ?, ?, ?)",
            [(1, "duplicate", "one", 1), (2, "DUPLICATE", "two", 2)],
        )
    with pytest.raises(StorageConflict):
        import_legacy(source, target, backup_directory=tmp_path / "backup")
    with target.session() as session:
        assert session.query(User).count() == 0
    assert source.exists()


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


def test_management_history_and_report_index_survive_restart_and_unmounted_data(orm, tmp_path):
    import json

    from sqlalchemy import select

    from api.db.models import BacktestReport, ManagementJob
    from tests.web.jobs_test import finished, module
    from web.jobs import JobManager
    from web.server import Console

    db, _, _ = orm
    directory = tmp_path / ".local/state/web/jobs"
    directory.mkdir(parents=True)
    identity = "a" * 32
    legacy = {
        "id": identity,
        "action": "check_quick",
        "status": "running",
        "created_at": "2026-01-01T00:00:00Z",
    }
    (directory / f"{identity}.json").write_text(json.dumps(legacy))
    logs = tmp_path / "logs/web"
    logs.mkdir(parents=True)
    (logs / f"{identity}.log").write_text("previous output")
    report = tmp_path / "data/reports/backtest/example/run/report.html"
    report.parent.mkdir(parents=True)
    report.write_text("report")
    jobs = JobManager(tmp_path, orm_database=db)
    assert jobs.get_job(identity)["status"] == "interrupted"
    assert jobs.get_job(identity)["log"] == "previous output"
    module(tmp_path, "web.jobs", "print('new output')\n")
    new_identity = jobs.start("check_quick", {})["id"]
    assert finished(jobs, new_identity)["status"] == "succeeded"
    assert len(Console(tmp_path, jobs=jobs).reports()) == 1
    jobs.close()
    with db.session() as session:
        assert session.get(ManagementJob, identity).payload["status"] == "interrupted"
        assert len(session.scalars(select(BacktestReport)).all()) == 1
    # A stale JSON snapshot must not overwrite an already migrated job.
    jobs = JobManager(tmp_path, orm_database=db)
    assert jobs.get_job(identity)["status"] == "interrupted"
    assert jobs.get_job(new_identity)["status"] == "succeeded"
    assert "new output" in jobs.get_job(new_identity)["log"]
    report.unlink()
    assert Console(tmp_path, jobs=jobs).reports() == []
    with db.session() as session:
        assert len(session.scalars(select(BacktestReport)).all()) == 1
    jobs.close()
