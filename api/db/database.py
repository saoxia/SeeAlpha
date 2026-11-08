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
                    expires_at INTEGER NOT NULL,
                    impersonate_user_id INTEGER REFERENCES users(id)
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
                CREATE TABLE IF NOT EXISTS tenant_templates (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    purpose TEXT NOT NULL CHECK (purpose IN ('production', 'development')),
                    name TEXT NOT NULL,
                    description TEXT,
                    created_at INTEGER NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS tenant_templates_name_unique
                    ON tenant_templates(lower(name));
                CREATE TABLE IF NOT EXISTS tenants (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    template_id INTEGER NOT NULL,
                    owner_id INTEGER REFERENCES users(id),
                    purpose TEXT NOT NULL CHECK (purpose IN ('production', 'development')),
                    name TEXT NOT NULL,
                    description TEXT,
                    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'archived')),
                    system_key TEXT,
                    created_at INTEGER NOT NULL,
                    archived_at INTEGER
                );
                CREATE UNIQUE INDEX IF NOT EXISTS tenants_name_unique ON tenants(lower(name));
                CREATE UNIQUE INDEX IF NOT EXISTS tenants_system_key_unique ON tenants(system_key);
                CREATE TABLE IF NOT EXISTS virtual_accounts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tenant_id INTEGER NOT NULL REFERENCES tenants(id),
                    account_name TEXT NOT NULL,
                    initial_capital TEXT NOT NULL,
                    quote_currency TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS virtual_accounts_name_unique
                    ON virtual_accounts(tenant_id, lower(account_name));
                CREATE TABLE IF NOT EXISTS exchange_accounts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tenant_id INTEGER NOT NULL REFERENCES tenants(id),
                    exchange TEXT NOT NULL,
                    account_name TEXT NOT NULL COLLATE NOCASE,
                    environment TEXT NOT NULL CHECK (environment IN ('production', 'sandbox')),
                    credentials_encrypted TEXT NOT NULL,
                    market_symbols_json TEXT NOT NULL DEFAULT '[]',
                    last_test_status TEXT CHECK (last_test_status IS NULL OR last_test_status IN ('success', 'failed')),
                    last_tested_at INTEGER,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    UNIQUE(tenant_id, exchange, account_name)
                );
                CREATE INDEX IF NOT EXISTS exchange_accounts_exchange_idx
                    ON exchange_accounts(exchange, created_at, id);
                CREATE TABLE IF NOT EXISTS strategy_packages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    code_key TEXT NOT NULL,
                    name TEXT NOT NULL,
                    description TEXT,
                    created_at INTEGER NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS strategy_packages_code_key_unique
                    ON strategy_packages(code_key);
                CREATE UNIQUE INDEX IF NOT EXISTS strategy_packages_name_unique
                    ON strategy_packages(lower(name));
                CREATE TABLE IF NOT EXISTS strategy_package_versions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    package_id INTEGER NOT NULL REFERENCES strategy_packages(id),
                    version TEXT NOT NULL,
                    parameters TEXT NOT NULL,
                    entry_point TEXT NOT NULL,
                    package_hash TEXT NOT NULL,
                    archive BLOB NOT NULL,
                    created_at INTEGER NOT NULL,
                    UNIQUE(package_id, version)
                );
                CREATE TABLE IF NOT EXISTS strategy_grants (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    version_id INTEGER NOT NULL REFERENCES strategy_package_versions(id),
                    purpose TEXT NOT NULL CHECK (purpose IN ('production', 'development')),
                    created_at INTEGER NOT NULL,
                    UNIQUE(version_id, purpose)
                );
                CREATE TABLE IF NOT EXISTS strategy_issuances (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tenant_id INTEGER NOT NULL REFERENCES tenants(id),
                    account_id TEXT NOT NULL,
                    version_id INTEGER NOT NULL REFERENCES strategy_package_versions(id),
                    purpose TEXT NOT NULL CHECK (purpose IN ('production', 'development')),
                    parameters TEXT NOT NULL,
                    created_at INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS strategy_issuances_tenant_idx
                    ON strategy_issuances(tenant_id, id);
            """)
            now = int(time.time())
            connection.execute(
                """INSERT OR IGNORE INTO tenant_templates (id, purpose, name, description, created_at)
                   VALUES (1, 'production', '空正式模板', NULL, ?)""",
                (now,),
            )
            connection.execute(
                """INSERT OR IGNORE INTO tenant_templates (id, purpose, name, description, created_at)
                   VALUES (2, 'development', '空开发模板', NULL, ?)""",
                (now,),
            )
            connection.execute(
                """INSERT OR IGNORE INTO tenants (
                       id, template_id, purpose, name, description, status, system_key, created_at
                   ) VALUES (1, 1, 'production', '默认正式', NULL, 'active', 'default', ?)""",
                (now,),
            )
            connection.execute(
                "INSERT OR IGNORE INTO api_schema_migrations VALUES (2, ?)", (int(time.time()),)
            )
            self._ensure_owner_columns(connection)

    @staticmethod
    def _column_names(connection, table):
        return {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}

    @classmethod
    def _ensure_owner_columns(cls, connection):
        """Add ownership columns when an older sqlite file is opened again."""
        if "owner_id" not in cls._column_names(connection, "tenants"):
            connection.execute("ALTER TABLE tenants ADD COLUMN owner_id INTEGER REFERENCES users(id)")
        if "impersonate_user_id" not in cls._column_names(connection, "sessions"):
            connection.execute(
                "ALTER TABLE sessions ADD COLUMN impersonate_user_id INTEGER REFERENCES users(id)"
            )
        connection.execute("CREATE INDEX IF NOT EXISTS tenants_owner_idx ON tenants(owner_id)")
        connection.execute(
            """CREATE UNIQUE INDEX IF NOT EXISTS tenants_one_production_per_owner
               ON tenants(owner_id) WHERE purpose = 'production'"""
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
