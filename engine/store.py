"""Engine-local SQLite (WAL). Secrets are never written."""

import json
import sqlite3
import threading
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS connections (
    account_id TEXT NOT NULL,
    environment TEXT NOT NULL,
    status TEXT NOT NULL,
    updated_at INTEGER NOT NULL,
    PRIMARY KEY (account_id, environment)
);
CREATE TABLE IF NOT EXISTS order_requests (
    account_id TEXT NOT NULL,
    request_id TEXT NOT NULL,
    body_json TEXT NOT NULL,
    updated_at INTEGER NOT NULL,
    PRIMARY KEY (account_id, request_id)
);
CREATE TABLE IF NOT EXISTS orders (
    account_id TEXT NOT NULL,
    environment TEXT NOT NULL,
    client_order_id TEXT NOT NULL,
    order_json TEXT NOT NULL,
    updated_at INTEGER NOT NULL,
    PRIMARY KEY (account_id, environment, client_order_id)
);
CREATE TABLE IF NOT EXISTS fills (
    account_id TEXT NOT NULL,
    environment TEXT NOT NULL,
    fill_id TEXT NOT NULL,
    fill_json TEXT NOT NULL,
    updated_at INTEGER NOT NULL,
    PRIMARY KEY (account_id, environment, fill_id)
);
CREATE TABLE IF NOT EXISTS positions (
    account_id TEXT NOT NULL,
    environment TEXT NOT NULL,
    position_id TEXT NOT NULL,
    position_json TEXT NOT NULL,
    updated_at INTEGER NOT NULL,
    PRIMARY KEY (account_id, environment, position_id)
);
CREATE TABLE IF NOT EXISTS gaps (
    account_id TEXT NOT NULL,
    scope TEXT NOT NULL,
    gap_key TEXT NOT NULL,
    gap_json TEXT NOT NULL,
    updated_at INTEGER NOT NULL,
    PRIMARY KEY (account_id, scope, gap_key)
);
CREATE TABLE IF NOT EXISTS subscriptions (
    account_id TEXT NOT NULL,
    product TEXT NOT NULL,
    symbol TEXT NOT NULL,
    role TEXT NOT NULL,
    state TEXT NOT NULL,
    progress_json TEXT NOT NULL,
    updated_at INTEGER NOT NULL,
    PRIMARY KEY (account_id, product, symbol, role)
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    body_json TEXT NOT NULL,
    created_at INTEGER NOT NULL
);
"""


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = None
        self.lock = threading.Lock()
        self._open()

    def _open(self):
        self.db = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)
        try:
            self.path.chmod(0o600)
        except OSError:
            pass

    def _ensure(self):
        if self.db is None:
            self._open()

    def close(self):
        with self.lock:
            if self.db is not None:
                self.db.close()
                self.db = None

    def _write(self, sql, params=()):
        with self.lock:
            self._ensure()
            self.db.execute(sql, params)

    def upsert_connection(self, account_id, environment, status):
        now = int(time.time() * 1000)
        self._write(
            """INSERT INTO connections (account_id, environment, status, updated_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(account_id, environment) DO UPDATE SET
                 status=excluded.status, updated_at=excluded.updated_at""",
            (str(account_id), environment, status, now),
        )
        return self.connection(account_id, environment)

    def connection(self, account_id, environment):
        self._ensure()
        row = self.db.execute(
            "SELECT * FROM connections WHERE account_id=? AND environment=?",
            (str(account_id), environment),
        ).fetchone()
        return dict(row) if row else None

    def get_request(self, account_id, request_id):
        self._ensure()
        row = self.db.execute(
            "SELECT body_json FROM order_requests WHERE account_id=? AND request_id=?",
            (str(account_id), str(request_id)),
        ).fetchone()
        return json.loads(row["body_json"]) if row else None

    def save_request(self, account_id, request_id, body):
        stored = {key: value for key, value in body.items() if key != "replay"}
        self._write(
            """INSERT INTO order_requests (account_id, request_id, body_json, updated_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(account_id, request_id) DO UPDATE SET
                 body_json=excluded.body_json, updated_at=excluded.updated_at""",
            (str(account_id), str(request_id), json.dumps(stored, ensure_ascii=False), int(time.time() * 1000)),
        )
        return stored

    def upsert_order(self, account_id, environment, order):
        now = int(time.time() * 1000)
        self._write(
            """INSERT INTO orders (account_id, environment, client_order_id, order_json, updated_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(account_id, environment, client_order_id) DO UPDATE SET
                 order_json=excluded.order_json, updated_at=excluded.updated_at""",
            (str(account_id), environment, order["client_order_id"], json.dumps(order, ensure_ascii=False), now),
        )

    def orders(self, account_id, environment):
        self._ensure()
        rows = self.db.execute(
            "SELECT order_json FROM orders WHERE account_id=? AND environment=? ORDER BY updated_at",
            (str(account_id), environment),
        ).fetchall()
        return [json.loads(row["order_json"]) for row in rows]

    def upsert_fill(self, account_id, environment, fill_id, fill):
        now = int(time.time() * 1000)
        self._write(
            """INSERT INTO fills (account_id, environment, fill_id, fill_json, updated_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(account_id, environment, fill_id) DO UPDATE SET
                 fill_json=excluded.fill_json, updated_at=excluded.updated_at""",
            (str(account_id), environment, str(fill_id), json.dumps(fill, ensure_ascii=False), now),
        )

    def fills(self, account_id, environment):
        self._ensure()
        rows = self.db.execute(
            "SELECT fill_json FROM fills WHERE account_id=? AND environment=? ORDER BY updated_at",
            (str(account_id), environment),
        ).fetchall()
        return [json.loads(row["fill_json"]) for row in rows]

    def upsert_position(self, account_id, environment, position):
        now = int(time.time() * 1000)
        self._write(
            """INSERT INTO positions (account_id, environment, position_id, position_json, updated_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(account_id, environment, position_id) DO UPDATE SET
                 position_json=excluded.position_json, updated_at=excluded.updated_at""",
            (str(account_id), environment, str(position["id"]), json.dumps(position, ensure_ascii=False), now),
        )

    def positions(self, account_id, environment):
        self._ensure()
        rows = self.db.execute(
            "SELECT position_json FROM positions WHERE account_id=? AND environment=? ORDER BY position_id",
            (str(account_id), environment),
        ).fetchall()
        return [json.loads(row["position_json"]) for row in rows]

    def upsert_gap(self, account_id, scope, gap_key, gap):
        now = int(time.time() * 1000)
        self._write(
            """INSERT INTO gaps (account_id, scope, gap_key, gap_json, updated_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(account_id, scope, gap_key) DO UPDATE SET
                 gap_json=excluded.gap_json, updated_at=excluded.updated_at""",
            (str(account_id), scope, gap_key, json.dumps(gap, ensure_ascii=False), now),
        )

    def gaps(self, account_id, scope=None):
        self._ensure()
        if scope is None:
            rows = self.db.execute(
                "SELECT gap_json FROM gaps WHERE account_id=? ORDER BY scope, gap_key", (str(account_id),)
            ).fetchall()
        else:
            rows = self.db.execute(
                "SELECT gap_json FROM gaps WHERE account_id=? AND scope=? ORDER BY gap_key",
                (str(account_id), scope),
            ).fetchall()
        return [json.loads(row["gap_json"]) for row in rows]

    def upsert_subscription(self, account_id, product, symbol, role, state, progress):
        now = int(time.time() * 1000)
        self._write(
            """INSERT INTO subscriptions
               (account_id, product, symbol, role, state, progress_json, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(account_id, product, symbol, role) DO UPDATE SET
                 state=excluded.state, progress_json=excluded.progress_json,
                 updated_at=excluded.updated_at""",
            (str(account_id), product, symbol, role, state, json.dumps(progress, ensure_ascii=False), now),
        )

    def subscriptions(self, account_id):
        self._ensure()
        rows = self.db.execute(
            """SELECT account_id, product, symbol, role, state, progress_json, updated_at
               FROM subscriptions WHERE account_id=? ORDER BY product, symbol, role""",
            (str(account_id),),
        ).fetchall()
        return [{**dict(row), "progress": json.loads(row["progress_json"])} for row in rows]

    def add_event(self, account_id, kind, body):
        now = int(time.time() * 1000)
        with self.lock:
            self._ensure()
            cursor = self.db.execute(
                "INSERT INTO events (account_id, kind, body_json, created_at) VALUES (?, ?, ?, ?)",
                (str(account_id), kind, json.dumps(body, ensure_ascii=False), now),
            )
            return cursor.lastrowid

    def events(self, account_id, after=0, limit=200):
        self._ensure()
        rows = self.db.execute(
            """SELECT id, kind, body_json, created_at FROM events
               WHERE account_id=? AND id>? ORDER BY id LIMIT ?""",
            (str(account_id), int(after), int(limit)),
        ).fetchall()
        return [
            {"id": row["id"], "kind": row["kind"], "body": json.loads(row["body_json"]), "created_at": row["created_at"]}
            for row in rows
        ]
