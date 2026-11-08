from __future__ import annotations

import sqlite3
import time

from .database import Database

EXCHANGE_ACCOUNT_COLUMNS = """
    id, tenant_id, exchange, account_name, environment, credentials_encrypted, market_symbols_json,
    last_test_status, last_tested_at, created_at, updated_at
"""


class ExchangeRepository:
    def __init__(self, database: Database):
        self.database = database

    def list_exchange_accounts(self, tenant_id: int | None = None) -> list[sqlite3.Row]:
        with self.database.connect() as connection:
            if tenant_id is None:
                return connection.execute(
                    f"""SELECT {EXCHANGE_ACCOUNT_COLUMNS} FROM exchange_accounts
                        ORDER BY exchange, account_name COLLATE NOCASE, id"""
                ).fetchall()
            return connection.execute(
                f"""SELECT {EXCHANGE_ACCOUNT_COLUMNS} FROM exchange_accounts
                    WHERE tenant_id = ?
                    ORDER BY exchange, account_name COLLATE NOCASE, id""",
                (tenant_id,),
            ).fetchall()

    def get_exchange_account(self, account_id: int) -> sqlite3.Row | None:
        with self.database.connect() as connection:
            return connection.execute(
                f"SELECT {EXCHANGE_ACCOUNT_COLUMNS} FROM exchange_accounts WHERE id = ?",
                (account_id,),
            ).fetchone()

    def create_exchange_account(
        self,
        *,
        tenant_id: int,
        exchange: str,
        account_name: str,
        environment: str,
        credentials_encrypted: str,
        market_symbols_json: str,
    ) -> sqlite3.Row:
        now = int(time.time())
        with self.database.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO exchange_accounts (
                    tenant_id, exchange, account_name, environment, credentials_encrypted,
                    market_symbols_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    tenant_id,
                    exchange,
                    account_name,
                    environment,
                    credentials_encrypted,
                    market_symbols_json,
                    now,
                    now,
                ),
            )
            return connection.execute(
                f"SELECT {EXCHANGE_ACCOUNT_COLUMNS} FROM exchange_accounts WHERE id = ?",
                (cursor.lastrowid,),
            ).fetchone()

    def update_exchange_account(
        self,
        account_id: int,
        *,
        account_name: str,
        environment: str,
        credentials_encrypted: str,
        market_symbols_json: str,
    ) -> sqlite3.Row | None:
        now = int(time.time())
        with self.database.connect() as connection:
            connection.execute(
                """
                UPDATE exchange_accounts SET account_name = ?, environment = ?,
                    credentials_encrypted = ?, market_symbols_json = ?,
                    last_test_status = NULL, last_tested_at = NULL, updated_at = ?
                WHERE id = ?
                """,
                (
                    account_name,
                    environment,
                    credentials_encrypted,
                    market_symbols_json,
                    now,
                    account_id,
                ),
            )
            return connection.execute(
                f"SELECT {EXCHANGE_ACCOUNT_COLUMNS} FROM exchange_accounts WHERE id = ?",
                (account_id,),
            ).fetchone()

    def update_exchange_account_test_result(self, account_id: int, *, succeeded: bool) -> None:
        with self.database.connect() as connection:
            connection.execute(
                """UPDATE exchange_accounts SET last_test_status = ?, last_tested_at = ?
                   WHERE id = ?""",
                ("success" if succeeded else "failed", int(time.time()), account_id),
            )

    def delete_exchange_account(self, account_id: int) -> bool:
        with self.database.connect() as connection:
            cursor = connection.execute("DELETE FROM exchange_accounts WHERE id = ?", (account_id,))
            return cursor.rowcount > 0


def exchange_repository(database):
    if database.backend == "orm":
        from .orm_repositories import ORMExchangeRepository

        return ORMExchangeRepository(database)
    return ExchangeRepository(database)
