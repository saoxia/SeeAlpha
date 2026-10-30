from __future__ import annotations

import sqlite3
import time
from typing import Any

from .database import Database


class AuthRepository:
    def __init__(self, database: Database):
        self.database = database

    def create_user(self, username: str, password_hash: str) -> dict[str, Any]:
        now = int(time.time())
        with self.database.connect() as connection:
            cursor = connection.execute(
                "INSERT INTO users (username, password_hash, created_at) VALUES (?, ?, ?)",
                (username, password_hash, now),
            )
            return {"id": cursor.lastrowid, "username": username, "created_at": now}

    def get_user_by_username(self, username: str) -> sqlite3.Row | None:
        with self.database.connect() as connection:
            return connection.execute(
                "SELECT id, username, password_hash, created_at FROM users WHERE username = ?",
                (username,),
            ).fetchone()

    def update_password(self, user_id: int, password_hash: str) -> None:
        with self.database.connect() as connection:
            connection.execute(
                "UPDATE users SET password_hash = ? WHERE id = ?", (password_hash, user_id)
            )

    def reset_password(self, user_id, password_hash):
        with self.database.connect() as connection:
            connection.execute(
                "UPDATE users SET password_hash = ? WHERE id = ?", (password_hash, user_id)
            )
            connection.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))

    def create_session(self, user_id: int, token_hash: str, expires_at: int) -> None:
        now = int(time.time())
        with self.database.connect() as connection:
            connection.execute("DELETE FROM sessions WHERE expires_at <= ?", (now,))
            connection.execute(
                "INSERT INTO sessions (token_hash, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
                (token_hash, user_id, now, expires_at),
            )

    def get_user_for_session(self, token_hash: str) -> sqlite3.Row | None:
        now = int(time.time())
        with self.database.connect() as connection:
            return connection.execute(
                """
                SELECT users.id, users.username, users.created_at, sessions.expires_at
                FROM sessions JOIN users ON users.id = sessions.user_id
                WHERE sessions.token_hash = ? AND sessions.expires_at > ?
                """,
                (token_hash, now),
            ).fetchone()

    def delete_session(self, token_hash: str) -> None:
        with self.database.connect() as connection:
            connection.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))

    def record_login_failure(self, identity_hash: str) -> None:
        now = int(time.time())
        with self.database.connect() as connection:
            connection.execute(
                "INSERT INTO login_failures (identity_hash, attempted_at) VALUES (?, ?)",
                (identity_hash, now),
            )

    def clear_login_failures(self, identity_hash: str) -> None:
        with self.database.connect() as connection:
            connection.execute(
                "DELETE FROM login_failures WHERE identity_hash = ?", (identity_hash,)
            )

    def is_login_rate_limited(
        self, identity_hash: str, *, limit: int = 5, window_seconds: int = 15 * 60
    ) -> bool:
        cutoff = int(time.time()) - window_seconds
        with self.database.connect() as connection:
            connection.execute("DELETE FROM login_failures WHERE attempted_at < ?", (cutoff,))
            count = connection.execute(
                "SELECT COUNT(*) FROM login_failures WHERE identity_hash = ? AND attempted_at >= ?",
                (identity_hash, cutoff),
            ).fetchone()[0]
        return count >= limit


def auth_repository(database):
    if database.backend == "orm":
        from .orm_repositories import ORMAuthRepository

        return ORMAuthRepository(database)
    return AuthRepository(database)
