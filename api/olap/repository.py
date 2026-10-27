import sqlite3
import time


class OlapRepository:
    def __init__(self, database):
        self.database = database

    def initialize(self):
        self.database.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.database.path.touch(mode=0o600, exist_ok=True)
        with self.database.connect() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS olap_settings (id INTEGER PRIMARY KEY CHECK (id=1), address TEXT NOT NULL, port INTEGER NOT NULL, database_name TEXT NOT NULL, username TEXT NOT NULL, password_encrypted TEXT NOT NULL, updated_at INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS clickhouse_monitor_settings (id INTEGER PRIMARY KEY CHECK (id=1), refresh_interval_seconds INTEGER NOT NULL, tables_json TEXT NOT NULL, updated_at INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS clickhouse_monitor_cache (id INTEGER PRIMARY KEY CHECK (id=1), payload_json TEXT NOT NULL, checked_at INTEGER NOT NULL, updated_at INTEGER NOT NULL);
            """)

    def get_olap_settings(self) -> sqlite3.Row | None:
        with self.database.connect() as connection:
            return connection.execute(
                """SELECT address, port, database_name, username, password_encrypted, updated_at
                   FROM olap_settings WHERE id = 1"""
            ).fetchone()

    def save_olap_settings(
        self,
        *,
        address: str,
        port: int,
        database_name: str,
        username: str,
        password_encrypted: str,
    ) -> sqlite3.Row:
        now = int(time.time())
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO olap_settings (
                    id, address, port, database_name, username, password_encrypted, updated_at
                ) VALUES (1, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET address = excluded.address, port = excluded.port,
                    database_name = excluded.database_name, username = excluded.username,
                    password_encrypted = excluded.password_encrypted, updated_at = excluded.updated_at
                """,
                (address, port, database_name, username, password_encrypted, now),
            )
            return connection.execute(
                """SELECT address, port, database_name, username, password_encrypted, updated_at
                   FROM olap_settings WHERE id = 1"""
            ).fetchone()

    def get_clickhouse_monitor_settings(self) -> sqlite3.Row | None:
        with self.database.connect() as connection:
            return connection.execute(
                """SELECT refresh_interval_seconds, tables_json, updated_at
                   FROM clickhouse_monitor_settings WHERE id = 1"""
            ).fetchone()

    def save_clickhouse_monitor_settings(
        self, *, refresh_interval_seconds: int, tables_json: str
    ) -> sqlite3.Row:
        now = int(time.time())
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO clickhouse_monitor_settings (
                    id, refresh_interval_seconds, tables_json, updated_at
                ) VALUES (1, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    refresh_interval_seconds = excluded.refresh_interval_seconds,
                    tables_json = excluded.tables_json, updated_at = excluded.updated_at
                """,
                (refresh_interval_seconds, tables_json, now),
            )
            return connection.execute(
                """SELECT refresh_interval_seconds, tables_json, updated_at
                   FROM clickhouse_monitor_settings WHERE id = 1"""
            ).fetchone()

    def get_clickhouse_monitor_cache(self) -> sqlite3.Row | None:
        with self.database.connect() as connection:
            return connection.execute(
                """SELECT payload_json, checked_at, updated_at
                   FROM clickhouse_monitor_cache WHERE id = 1"""
            ).fetchone()

    def save_clickhouse_monitor_cache(self, *, payload_json: str, checked_at: int) -> None:
        now = int(time.time())
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO clickhouse_monitor_cache (id, payload_json, checked_at, updated_at)
                VALUES (1, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET payload_json = excluded.payload_json,
                    checked_at = excluded.checked_at, updated_at = excluded.updated_at
                """,
                (payload_json, checked_at, now),
            )

    def delete_clickhouse_monitor_cache(self) -> None:
        with self.database.connect() as connection:
            connection.execute("DELETE FROM clickhouse_monitor_cache WHERE id = 1")


def olap_repository(database):
    if database.backend == "orm":
        from ..db.orm_repositories import ORMOlapRepository

        return ORMOlapRepository(database)
    return OlapRepository(database)
