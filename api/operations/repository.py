"""Immutable observations with a deduplicated project view, for the legacy SQLite store."""

import hashlib
import json
import time
import uuid

from .models import Observation

SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS ledger_operations (
    id TEXT PRIMARY KEY,
    account_id INTEGER NOT NULL,
    environment TEXT NOT NULL,
    exchange TEXT NOT NULL,
    symbol TEXT NOT NULL,
    event_type TEXT NOT NULL,
    timestamp INTEGER NOT NULL,
    client_order_id TEXT,
    order_id TEXT,
    trade_id TEXT,
    source TEXT NOT NULL,
    strategy_id TEXT,
    body TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ledger_time ON ledger_operations(timestamp DESC,id DESC);
CREATE INDEX IF NOT EXISTS ledger_account_time ON ledger_operations(account_id,environment,timestamp DESC,id DESC);
CREATE INDEX IF NOT EXISTS ledger_order ON ledger_operations(account_id,environment,exchange,symbol,order_id);
CREATE INDEX IF NOT EXISTS ledger_client ON ledger_operations(account_id,environment,exchange,symbol,client_order_id);
CREATE UNIQUE INDEX IF NOT EXISTS ledger_fill ON ledger_operations(account_id,environment,exchange,symbol,trade_id) WHERE event_type='fill';
CREATE TABLE IF NOT EXISTS ledger_observations (
    id TEXT PRIMARY KEY, operation_id TEXT NOT NULL REFERENCES ledger_operations(id), body TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ledger_observation_operation ON ledger_observations(operation_id);
CREATE TABLE IF NOT EXISTS ledger_aliases (
    id TEXT PRIMARY KEY, operation_id TEXT NOT NULL REFERENCES ledger_operations(id)
);
CREATE TABLE IF NOT EXISTS ledger_sync (
    account_id INTEGER NOT NULL, environment TEXT NOT NULL, body TEXT NOT NULL,
    PRIMARY KEY(account_id,environment)
);
"""


def observation_key(record):
    # Hash only identity, not values: replays remain idempotent; conflicting values are an error.
    identity = [
        record[k]
        for k in ("account_id", "environment", "exchange", "symbol", "evidence", "event_id")
    ]
    return hashlib.sha256(json.dumps(identity, separators=(",", ":")).encode()).hexdigest()


def same_order(left, right):
    if left.get("order_id") and right.get("order_id"):
        return left["order_id"] == right["order_id"]
    return any(left.get(k) and left.get(k) == right.get(k) for k in ("order_id", "client_order_id"))


class Operations:
    def __init__(self, database):
        self.database = database

    def initialize(self):
        with self.database.connect() as c:
            c.executescript(SQLITE_SCHEMA)

    def ingest(self, observation):
        record = Observation.model_validate(observation).model_dump()
        key = observation_key(record)
        with self.database.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            previous = c.execute(
                "SELECT operation_id,body FROM ledger_observations WHERE id=?", (key,)
            ).fetchone()
            if previous:
                if json.loads(previous["body"]) != record:
                    raise ValueError("相同事件标识包含冲突的内容，请使用新的事件标识保留修订。")
                return {
                    "operation_id": previous["operation_id"],
                    "created": False,
                    "observation_added": False,
                }
            candidates = [
                json.loads(r["body"])
                for r in c.execute(
                    "SELECT body FROM ledger_operations WHERE account_id=? AND environment=? AND exchange=? AND symbol=?",
                    tuple(record[k] for k in ("account_id", "environment", "exchange", "symbol")),
                )
            ]
            linked = [r for r in candidates if same_order(r, record)]
            if record.get("strategy_id") and any(
                r.get("strategy_id") and r["strategy_id"] != record["strategy_id"] for r in linked
            ):
                raise ValueError("同一订单的策略归属存在冲突。")
            matches = [
                r
                for r in candidates
                if r["event_type"] == record["event_type"]
                and (
                    (record["event_type"] == "fill" and r["trade_id"] == record["trade_id"])
                    or (
                        record["event_type"] != "fill"
                        and same_order(r, record)
                        and (
                            record["event_type"] not in ("amend", "correction")
                            or r["timestamp"] == record["timestamp"]
                        )
                    )
                )
            ]
            matches.sort(key=lambda r: (r["source"] != "project", r["timestamp"], r["id"]))
            old = matches[0] if matches else None
            if old:
                for duplicate in matches[1:]:
                    if duplicate["confirmed"] and not old["confirmed"]:
                        for field in (
                            "amount",
                            "price",
                            "cost",
                            "filled",
                            "fee_cost",
                            "fee_currency",
                            "status",
                            "confirmed",
                        ):
                            old[field] = duplicate.get(field)
                    c.execute(
                        "UPDATE ledger_observations SET operation_id=? WHERE operation_id=?",
                        (old["id"], duplicate["id"]),
                    )
                    c.execute(
                        "UPDATE ledger_aliases SET operation_id=? WHERE operation_id=?",
                        (old["id"], duplicate["id"]),
                    )
                    c.execute(
                        "INSERT INTO ledger_aliases VALUES (?,?) ON CONFLICT(id) DO UPDATE SET operation_id=excluded.operation_id",
                        (duplicate["id"], old["id"]),
                    )
                    c.execute("DELETE FROM ledger_operations WHERE id=?", (duplicate["id"],))
            # A late project event can identify already imported venue fills for the same order.
            strategy = record.get("strategy_id") or next(
                (r.get("strategy_id") for r in linked if r["source"] == "project"), None
            )
            project = record["evidence"] == "project" or any(
                r["source"] == "project" for r in linked
            )
            source = (
                "reconciliation"
                if record["evidence"] == "reconciliation"
                else ("project" if project else "external")
            )
            body = {
                **(old or record),
                **{k: v for k, v in record.items() if v is not None and v != ""},
                "id": old["id"] if old else str(uuid.uuid4()),
                "source": source,
                "strategy_id": strategy,
                "confirmed": bool(record["evidence"] == "venue" or (old or {}).get("confirmed")),
            }
            if old:
                body["timestamp"] = old["timestamp"]
                # Venue facts remain authoritative when project evidence arrives late.
                if old["confirmed"] and record["evidence"] != "venue":
                    for k in (
                        "amount",
                        "price",
                        "cost",
                        "filled",
                        "fee_cost",
                        "fee_currency",
                        "status",
                    ):
                        body[k] = old.get(k)
            self._save(c, body)
            c.execute(
                "INSERT INTO ledger_observations VALUES (?,?,?)",
                (key, body["id"], json.dumps(record)),
            )
            if project:
                for r in linked:
                    if (
                        r["id"] == body["id"]
                        or r["id"] in {item["id"] for item in matches[1:]}
                        or r["source"] == "reconciliation"
                    ):
                        continue
                    r.update(source="project", strategy_id=strategy or r.get("strategy_id"))
                    self._save(c, r)
            return {"operation_id": body["id"], "created": old is None, "observation_added": True}

    @staticmethod
    def _save(c, body):
        names = (
            "id",
            "account_id",
            "environment",
            "exchange",
            "symbol",
            "event_type",
            "timestamp",
            "client_order_id",
            "order_id",
            "trade_id",
            "source",
            "strategy_id",
        )
        c.execute(
            f"INSERT INTO ledger_operations ({','.join(names)},body) VALUES ({','.join('?' for _ in names)},?) ON CONFLICT(id) DO UPDATE SET "
            + ",".join(f"{n}=excluded.{n}" for n in (*names[1:], "body")),
            (*[body.get(k) for k in names], json.dumps(body)),
        )

    def list(self, *, cursor=None, limit=50, **filters):
        clauses, args = [], []
        for k, v in filters.items():
            if v is None or v == "":
                continue
            if k in ("account_id", "environment", "strategy_id", "source", "event_type"):
                clauses.append(f"{k}=?")
                args.append(v)
            elif k == "symbol":
                clauses.append("instr(upper(symbol),upper(?))>0")
                args.append(v)
            elif k in ("start_ms", "end_ms"):
                clauses.append(f"timestamp {'>=' if k == 'start_ms' else '<='} ?")
                args.append(v)
        if cursor:
            clauses.append("(timestamp < ? OR (timestamp = ? AND id < ?))")
            args.extend([cursor[0], cursor[0], cursor[1]])
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self.database.connect() as c:
            rows = c.execute(
                "SELECT body FROM ledger_operations"
                + where
                + " ORDER BY timestamp DESC,id DESC LIMIT ?",
                (*args, limit + 1),
            ).fetchall()
        records = [json.loads(r["body"]) for r in rows[:limit]]
        return {"records": records, "has_more": len(rows) > limit}

    def detail(self, operation_id):
        with self.database.connect() as c:
            alias = c.execute(
                "SELECT operation_id FROM ledger_aliases WHERE id=?", (operation_id,)
            ).fetchone()
            if alias:
                operation_id = alias["operation_id"]
            row = c.execute(
                "SELECT body FROM ledger_operations WHERE id=?", (operation_id,)
            ).fetchone()
            if not row:
                return None
            observations = [
                json.loads(r["body"])
                for r in c.execute(
                    "SELECT body FROM ledger_observations WHERE operation_id=? ORDER BY id",
                    (operation_id,),
                )
            ]
        return {"operation": json.loads(row["body"]), "observations": observations}

    def sync_state(self, account_id=None):
        with self.database.connect() as c:
            rows = c.execute(
                "SELECT body FROM ledger_sync" + (" WHERE account_id=?" if account_id else ""),
                (account_id,) if account_id else (),
            ).fetchall()
        return [json.loads(r["body"]) for r in rows]

    def save_sync(self, **state):
        state["checked_at"] = int(time.time() * 1000)
        with self.database.connect() as c:
            c.execute(
                "INSERT INTO ledger_sync VALUES (?,?,?) ON CONFLICT(account_id,environment) DO UPDATE SET body=excluded.body",
                (state["account_id"], state["environment"], json.dumps(state)),
            )
