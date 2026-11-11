"""Portable ORM persistence, session, management and restart regression suite.

Exercises SQLite and optionally an isolated TEST_POSTGRES_URL. API lifespan
uses only a strict local Engine bootstrap stub; no native trading dependencies.
"""

import os
import time
import uuid

import pytest
from alembic import command
from alembic.config import Config
from api.auth.security import hash_password
from api.config import Settings
from api.db.auth_repository import auth_repository
from api.db.errors import StorageConflict, StorageUnavailable
from api.db.exchange_repository import exchange_repository
from api.db.orm import ORMDatabase
from api.main import create_app
from api.olap.repository import olap_repository
from api.operations.orm_repository import ORMOperations
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy import event as sa_event

pytestmark = pytest.mark.usefixtures("platform_engine_bootstrap")


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


def event(**values):
    return {
        "event_id": "submit-1",
        "account_id": 1,
        "account_name": "main",
        "exchange": "binance",
        "environment": "production",
        "symbol": "ETHUSDT-PERP",
        "event_type": "place",
        "timestamp": 1000,
        "evidence": "project",
        "client_order_id": "client-1",
        "strategy_id": "strategy-1",
        "amount": "1",
        "price": "2000",
    } | values


def module(project, name, source):
    """Create a local-only check module, without importing legacy Web tests."""
    pieces = name.split(".")
    root = project
    for piece in pieces[:-1]:
        root /= piece
        root.mkdir(exist_ok=True)
        (root / "__init__.py").touch()
    (root / (pieces[-1] + ".py")).write_text(source, encoding="utf-8")


def finished(manager, identity):
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        job = manager.get_job(identity)
        if job["status"] != "running":
            return job
        time.sleep(0.02)
    pytest.fail("Child job did not finish")


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
        tenant_id=1,
        exchange="binance",
        account_name="Main",
        environment="production",
        credentials_encrypted="ciphertext",
        market_symbols_json="[]",
    )
    assert accounts.get_exchange_account(account["id"])["credentials_encrypted"] == "ciphertext"
    with pytest.raises(StorageConflict):
        accounts.create_exchange_account(
            tenant_id=1,
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
        response = client.post("/api/system/exchange-accounts", json={
            "exchange": "binance", "account_name": "No watchlist", "environment": "production",
            "api_key": "fixture-key", "secret": "fixture-secret",
        })
        assert response.status_code == 201
        account_id = response.json()["id"]
        assert response.json()["market_symbols"] == []
        cookie = client.cookies.get("seealpha_session")
    with TestClient(create_app(settings)) as client:
        client.cookies.set("seealpha_session", cookie)
        assert client.get("/api/auth/me").status_code == 200
        assert client.get("/api/system/exchange-accounts").status_code == 200
        before = exchange_repository(db).get_exchange_account(account_id)
        exchange_repository(db).update_exchange_account(
            account_id, account_name=before["account_name"], environment=before["environment"],
            credentials_encrypted=before["credentials_encrypted"],
            market_symbols_json='["ETH/USDT:USDT"]',
        )
        response = client.put(f"/api/system/exchange-accounts/{account_id}", json={
            "account_name": "Renamed", "environment": "production",
        })
        assert response.status_code == 200
        assert response.json()["market_symbols"] == ["ETH/USDT:USDT"]
        after = exchange_repository(db).get_exchange_account(account_id)
        assert after["credentials_encrypted"] == before["credentials_encrypted"]
        assert after["market_symbols_json"] == '["ETH/USDT:USDT"]'


def test_orm_ledger_idempotence_and_merge(orm):
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


def test_management_history_and_report_index_survive_restart_and_unmounted_data(orm, tmp_path):
    import json

    from api.db.models import BacktestReport, ManagementJob
    from api.management.jobs import CHECK_MODULE, JobManager
    from api.management.service import ManagementService
    from sqlalchemy import select

    db, _, _ = orm
    from api.db.management_repository import ManagementRepository

    directory = tmp_path / ".local/state/web/jobs"
    directory.mkdir(parents=True)
    identity = "a" * 32
    running = {
        "id": identity,
        "action": "check_quick",
        "status": "running",
        "created_at": "2026-01-01T00:00:00Z",
    }
    ManagementRepository(db).save_job(dict(running))
    # The pre-ORM JSON file is no longer imported into the business database.
    (directory / f"{identity}.json").write_text(json.dumps({**running, "status": "failed"}))
    logs = tmp_path / "logs/web"
    logs.mkdir(parents=True)
    (logs / f"{identity}.log").write_text("previous output")
    report = tmp_path / "data/reports/backtest/example/run/report.html"
    report.parent.mkdir(parents=True)
    report.write_text("report")
    jobs = JobManager(tmp_path, orm_database=db)
    assert jobs.get_job(identity)["status"] == "interrupted"
    assert jobs.get_job(identity)["log"] == "previous output"
    module(tmp_path, CHECK_MODULE, "print('new output')\n")
    new_identity = jobs.start("check_quick", {})["id"]
    assert finished(jobs, new_identity)["status"] == "succeeded"
    assert len(ManagementService(tmp_path, jobs).reports()) == 1
    jobs.close()
    with db.session() as session:
        assert session.get(ManagementJob, identity).payload["status"] == "interrupted"
        assert len(session.scalars(select(BacktestReport)).all()) == 1
    # A stale JSON snapshot must never overwrite an ORM job.
    jobs = JobManager(tmp_path, orm_database=db)
    assert jobs.get_job(identity)["status"] == "interrupted"
    assert jobs.get_job(new_identity)["status"] == "succeeded"
    assert "new output" in jobs.get_job(new_identity)["log"]
    report.unlink()
    assert ManagementService(tmp_path, jobs).reports() == []
    with db.session() as session:
        assert len(session.scalars(select(BacktestReport)).all()) == 1
    jobs.close()


def test_non_owner_reads_database_jobs_without_interrupting_them(orm, tmp_path):
    import fcntl
    import json

    from api.db.management_repository import ManagementRepository
    from api.db.models import ManagementJob
    from api.management.jobs import NotOwnerError
    from api.management.service import management_runtime
    from api.management.storage import WebPaths

    db, _, _ = orm
    identity = "c" * 32
    running = {
        "id": identity,
        "action": "check_quick",
        "status": "running",
        "created_at": "2026-08-01T00:00:00+00:00",
    }
    ManagementRepository(db).save_job(dict(running))
    paths = WebPaths(tmp_path)
    paths.jobs.mkdir(parents=True)
    (paths.jobs / f"{identity}.json").write_text(json.dumps({**running, "status": "failed"}))
    paths.logs.mkdir(parents=True)
    (paths.logs / f"{identity}.log").write_text("from the owner\n")
    report = tmp_path / "data/reports/backtest/example/run/report.html"
    report.parent.mkdir(parents=True)
    report.write_text("<p>Report</p>")
    (report.with_name("summary.json")).write_text("{}")
    paths.lock.parent.mkdir(parents=True)
    with paths.lock.open("a") as held:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with management_runtime(tmp_path, db) as service:
            assert service.jobs.executor is False
            assert service.jobs.get_job(identity)["status"] == "running"
            assert service.jobs.get_job(identity)["log"] == "from the owner\n"
            assert service.state()["report_count"] == 1
            with db.session() as session:
                assert session.get(ManagementJob, identity).payload["status"] == "running"
            ManagementRepository(db).save_job({**running, "status": "succeeded"})
            assert service.jobs.get_job(identity)["status"] == "succeeded"
            with pytest.raises(NotOwnerError, match="管理任务锁"):
                service.jobs.start("check_quick", {})
            with pytest.raises(NotOwnerError):
                service.jobs.cancel(identity)
            with pytest.raises(ValueError):
                service.jobs.start("check_quick", {"root": "/tmp"})
    assert json.loads((paths.jobs / f"{identity}.json").read_text())["status"] == "failed"
    with db.session() as session:
        assert session.get(ManagementJob, identity).payload["status"] == "succeeded"


def test_orm_session_lookup_is_read_only_with_immediate_expiry_and_revocation(orm):
    db, _url, _schema = orm
    auth = auth_repository(db)
    user = auth.create_user("session-reader", "fixture-hash")
    now = int(time.time())
    auth.create_session(user["id"], "valid-read-only", now + 60)
    auth.create_session(user["id"], "expired-read-only", now - 1)
    statements = []
    def record(_conn, _cursor, statement, _parameters, _context, _executemany):
        statements.append(statement.lstrip().upper())
    sa_event.listen(db.engine, "before_cursor_execute", record)
    try:
        assert auth.get_user_for_session("valid-read-only")["id"] == user["id"]
        assert auth.get_user_for_session("expired-read-only") is None
    finally:
        sa_event.remove(db.engine, "before_cursor_execute", record)
    assert statements and all(statement.startswith("SELECT") for statement in statements)
    auth.delete_session("valid-read-only")
    assert auth.get_user_for_session("valid-read-only") is None
    from api.db.models import Session
    with db.session() as session:
        assert session.get(Session, "expired-read-only") is not None
    auth.create_session(user["id"], "next-login", now + 60)
    with db.session() as session:
        assert session.get(Session, "expired-read-only") is None


def test_exhausted_connection_pool_becomes_service_unavailable(orm):
    from sqlalchemy.exc import TimeoutError as PoolTimeout
    db, _url, _schema = orm
    with pytest.raises(StorageUnavailable), db.session():
        raise PoolTimeout("pool exhausted")


def test_pool_replaces_closed_idle_connection_without_losing_sessions(orm):
    db, _url, _schema = orm
    auth = auth_repository(db)
    user = auth.create_user("reconnect-user", "hash")
    auth.create_session(user["id"], "reconnect-session", int(time.time()) + 60)
    with db.engine.connect() as connection:
        raw = connection.connection.dbapi_connection
    # Simulate the server closing an idle socket after it was returned to the pool.
    raw.close()
    assert auth.get_user_for_session("reconnect-session")["id"] == user["id"]
