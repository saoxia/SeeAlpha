"""Authentication schema, compatible with the legacy business backend during migration."""

import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path


class Database:
    backend = "sqlite"

    def __init__(self, path: Path):
        self.path = path

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def initialize(self):
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path.touch(mode=0o600, exist_ok=True)
        self.path.chmod(0o600)
        with self.connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS api_schema_migrations (
                    version INTEGER PRIMARY KEY,
                    applied_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                    password_hash TEXT NOT NULL,
                    created_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    token_hash TEXT PRIMARY KEY,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    created_at INTEGER NOT NULL,
                    expires_at INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS sessions_expires_at_idx ON sessions(expires_at);
                CREATE TABLE IF NOT EXISTS login_failures (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    identity_hash TEXT NOT NULL,
                    attempted_at INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS login_failures_lookup_idx
                    ON login_failures(identity_hash, attempted_at);
            """)
            connection.execute(
                "INSERT OR IGNORE INTO api_schema_migrations VALUES (1, ?)", (int(time.time()),)
            )
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS exchange_accounts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    exchange TEXT NOT NULL,
                    account_name TEXT NOT NULL COLLATE NOCASE,
                    environment TEXT NOT NULL CHECK (environment IN ('production', 'sandbox')),
                    credentials_encrypted TEXT NOT NULL,
                    market_symbols_json TEXT NOT NULL DEFAULT '[]',
                    last_test_status TEXT CHECK (last_test_status IS NULL OR last_test_status IN ('success', 'failed')),
                    last_tested_at INTEGER,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    UNIQUE(exchange, account_name)
                );
                CREATE INDEX IF NOT EXISTS exchange_accounts_exchange_idx
                    ON exchange_accounts(exchange, created_at, id);
            """)
            connection.execute(
                "INSERT OR IGNORE INTO api_schema_migrations VALUES (2, ?)", (int(time.time()),)
            )

    def close(self):
        pass


def create_database(settings):
    if settings.database_url:
        from .orm import ORMDatabase

        return ORMDatabase(
            settings.database_url, settings.database_path, schema=settings.database_schema
        )
    if settings.require_database_url:
        return UnconfiguredDatabase(settings.database_path)
    return Database(settings.database_path)


class UnconfiguredDatabase:
    """Fail closed until the application has an explicit database connection."""

    backend = "unconfigured"

    def __init__(self, path):
        self.path = path

    def initialize(self):
        from .errors import StorageUnavailable

        raise StorageUnavailable("请先配置 DATABASE_URL 或数据库连接文件，再启动 API。")

    def close(self):
        pass
