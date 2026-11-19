"""Durable, task-bound Platform research capabilities; never Engine admin credentials.

Opt-in local development controller. SQLite transactions serialize budgets across
API workers sharing the same private file; no new service or public grant mint.
"""
from __future__ import annotations

import hashlib
import os
import secrets
import sqlite3
import stat
import time
from contextlib import contextmanager
from pathlib import Path

from fastapi import HTTPException

from core.engine_contracts import EngineConflict

from ..market_data.repository import PolicyRepository
from ..tenant_docker import DockerTenantManager
from .direct_engine import DirectEngineResearchClient
from .grants import ResearchGrantDenied, ResearchGrantIssuer

OPERATIONS = {
    "datasets": "datasets:read",
    "snapshot_create": "snapshots:create",
    "snapshot_get": "snapshots:read",
    "snapshot_fetch": "snapshots:read",
    "backtest_submit": "backtests:submit",
    "backtest_get": "backtests:read",
    "backtest_cancel": "backtests:cancel",
}
_TTL = 900
_MAX_CALLS = 80


class ResearchLeases:
    """One private SQLite journal, never shared with Agent or Engine."""

    def __init__(self, path: Path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if path.parent.is_symlink() or stat.S_IMODE(path.parent.stat().st_mode) & 0o077:
            raise ValueError("Research lease directory must be private")
        if path.is_symlink() or (path.exists() and stat.S_IMODE(path.stat().st_mode) & 0o077):
            raise ValueError("Research lease file must be private and cannot be a symlink")
        if not path.exists():
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
            try:
                descriptor = os.open(path, flags, 0o600)
            except FileExistsError:
                pass
            else:
                os.close(descriptor)
        if path.is_symlink() or stat.S_IMODE(path.stat().st_mode) & 0o077:
            raise ValueError("Research lease storage permissions unsafe")
        self.path = path
        with self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS research_leases (
                  task_id TEXT PRIMARY KEY, owner_id INTEGER NOT NULL,
                  tenant_id INTEGER NOT NULL, account_id TEXT NOT NULL,
                  dataset_ref TEXT NOT NULL, turn INTEGER NOT NULL,
                  token_sha256 TEXT NOT NULL, expires_at INTEGER NOT NULL,
                  remaining INTEGER NOT NULL, active INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS research_audit (
                  id INTEGER PRIMARY KEY, ts INTEGER NOT NULL,
                  task_id TEXT NOT NULL, event TEXT NOT NULL,
                  detail TEXT NOT NULL DEFAULT ''
                );
            """)

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def issue(self, *, task_id: str, owner_id: int, tenant_id: int,
              account_id: str, dataset_ref: str, turn: int) -> tuple[str, int]:
        token = secrets.token_urlsafe(48)
        expiry = int(time.time()) + _TTL
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute(
                "SELECT turn, owner_id FROM research_leases WHERE task_id = ?", (task_id,)
            ).fetchone()
            if existing and (existing["owner_id"] != owner_id or existing["turn"] >= turn):
                raise HTTPException(409, "Research task lease already exists")
            db.execute(
                """INSERT INTO research_leases VALUES (?,?,?,?,?,?,?,?,?,1)
                ON CONFLICT(task_id) DO UPDATE SET
                  owner_id=excluded.owner_id, tenant_id=excluded.tenant_id,
                  account_id=excluded.account_id, dataset_ref=excluded.dataset_ref,
                  turn=excluded.turn, token_sha256=excluded.token_sha256,
                  expires_at=excluded.expires_at, remaining=excluded.remaining, active=1""",
                (task_id, owner_id, tenant_id, account_id, dataset_ref, turn,
                 hashlib.sha256(token.encode()).hexdigest(), expiry, _MAX_CALLS),
            )
            db.execute(
                "INSERT INTO research_audit(ts,task_id,event) VALUES(?,?,?)",
                (int(time.time()), task_id, "issued"),
            )
        return token, expiry

    def owner_scope(self, task_id: str, owner_id: int):
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM research_leases WHERE task_id=? AND owner_id=?",
                (task_id, owner_id),
            ).fetchone()
        return dict(row) if row else None

    def authenticated(self, task_id: str, token: str) -> dict:
        if not isinstance(token, str) or not 40 <= len(token) <= 256:
            raise HTTPException(401, "Research capability required")
        digest = hashlib.sha256(token.encode()).hexdigest()
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM research_leases WHERE task_id=?", (task_id,)
            ).fetchone()
        if (
            row is None or not secrets.compare_digest(digest, row["token_sha256"])
            or row["active"] != 1 or row["expires_at"] <= int(time.time())
        ):
            raise HTTPException(403, "Research capability denied or expired")
        return dict(row)

    def consume(self, task_id: str, token: str, turn: int, operation: str) -> dict:
        if operation not in OPERATIONS:
            raise HTTPException(404, "Unknown research operation")
        digest = hashlib.sha256(token.encode()).hexdigest()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM research_leases WHERE task_id=?", (task_id,)
            ).fetchone()
            if (
                row is None or not secrets.compare_digest(digest, row["token_sha256"])
                or row["active"] != 1 or row["expires_at"] <= int(time.time())
                or row["turn"] != turn
            ):
                raise HTTPException(403, "Research capability denied or expired")
            if row["remaining"] <= 0:
                raise HTTPException(429, "Research call budget exhausted")
            db.execute(
                "UPDATE research_leases SET remaining=remaining-1 WHERE task_id=?",
                (task_id,),
            )
            db.execute(
                "INSERT INTO research_audit(ts,task_id,event,detail) VALUES(?,?,?,?)",
                (int(time.time()), task_id, "command", operation),
            )
        return dict(row)

    def revoke(self, task_id: str) -> None:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("UPDATE research_leases SET active=0 WHERE task_id=?", (task_id,))
            db.execute(
                "INSERT INTO research_audit(ts,task_id,event) VALUES(?,?,?)",
                (int(time.time()), task_id, "revoked"),
            )

    def audit(self, event: dict) -> None:
        with self._connect() as db:
            db.execute(
                "INSERT INTO research_audit(ts,task_id,event,detail) VALUES(?,?,?,?)",
                (int(time.time()), event["task_id"], event["event"],
                 event.get("fingerprint", "")),
            )


def allowed_scope(app, owner_id: int, tenant_id: int, account_id: str,
                  dataset_ref: str) -> bool:
    """Authoritative business grants: tenant ownership, policy, issued account."""
    try:
        tenant = app.state.tenants.repository.get_tenant(tenant_id)
        if (
            tenant is None or tenant["owner_id"] != owner_id
            or tenant["status"] != "active" or tenant["purpose"] != "development"
            or not app.state.user_resources.enabled(owner_id, "harness")
            or not app.state.user_resources.enabled(owner_id, "development")
        ):
            return False
        market, symbol, interval = dataset_ref.split(":")
        if market != "futures_um" or interval not in {"1m", "5m"}:
            return False
        accounts = app.state.tenants.repository.list_virtual_accounts(tenant_id)
        if str(account_id) not in {str(row["id"]) for row in accounts}:
            return False
        policies = PolicyRepository(app.state.database).records(tenant_id)
        if not any(
            row["product"] == market and row["symbol"] == symbol
            and row["parameters"].get("history_enabled") is True
            and interval in row["parameters"].get("intervals", [])
            for row in policies
        ):
            return False
        loads = app.state.strategies.repository.list_issuances(tenant_id)
        selected = [row for row in loads if row["account_id"] == str(account_id)]
        return bool(selected) and all(
            row["purpose"] == "development"
            and row["parameters"].get("symbol") == symbol for row in selected
        )
    except Exception:  # noqa: BLE001 - any authority store failure must deny access
        return False


def engine_connection(app, tenant_id: int) -> tuple[str, str]:
    """Only locally pinned development Engines with an actually installed key."""
    deployment = app.state.production_deployments
    manager = deployment.manager if deployment is not None else None
    if not isinstance(manager, DockerTenantManager):
        raise HTTPException(503, "Research needs a local pinned development Engine")
    try:
        origin, _admin_token = manager.endpoint(tenant_id)
        key = manager.research_signing_key(tenant_id)
    except (OSError, ValueError, EngineConflict):
        raise HTTPException(503, "Development Engine research is not provisioned") from None
    return origin, key


def research_command(app, lease: dict, operation: str, args: dict):
    """Issue a per-operation grant, then call a fixed Engine research endpoint."""
    if not allowed_scope(
        app, lease["owner_id"], lease["tenant_id"],
        lease["account_id"], lease["dataset_ref"],
    ):
        raise HTTPException(403, "Research scope no longer authorized")
    origin, key = engine_connection(app, lease["tenant_id"])
    account = "account:" + lease["account_id"]
    dataset = lease["dataset_ref"]
    issuer = ResearchGrantIssuer(
        tenant_repository=app.state.tenants.repository,
        signing_key_for_tenant=lambda _tenant: key,
        authorize_resources=lambda _tenant, datasets, strategies: (
            datasets == (dataset,) and (not strategies or strategies == (account,))
        ),
        record_audit=app.state.research_leases.audit,
        forbidden_key=app.state.settings.engine_token,
    )
    try:
        grant = issuer.issue(
            actor_user_id=lease["owner_id"], tenant_id=lease["tenant_id"],
            task_id=lease["task_id"], operations=(OPERATIONS[operation],),
            datasets=(dataset,), strategies=(account,), ttl_seconds=60,
        )
    except ResearchGrantDenied:
        raise HTTPException(403, "Research authorization unavailable") from None
    with DirectEngineResearchClient(origin, grant) as client:
        if operation == "datasets":
            if args:
                raise ValueError("Datasets command takes no parameters")
            return client.discover_datasets()
        if operation == "snapshot_create":
            if set(args) != {"start", "end"}:
                raise ValueError("Invalid snapshot request")
            return client.create_snapshot(dataset, args["start"], args["end"])
        if operation == "snapshot_get":
            if set(args) != {"snapshot_id"}:
                raise ValueError("Invalid snapshot lookup")
            return client.get_snapshot(args["snapshot_id"])
        if operation == "snapshot_fetch":
            if set(args) != {"snapshot_id"}:
                raise ValueError("Invalid snapshot fetch")
            return client.fetch_snapshot(args["snapshot_id"])
        if operation == "backtest_submit":
            fields = {
                "start", "end", "starting_balance", "quote_currency", "idempotency_key",
            }
            if set(args) != fields:
                raise ValueError("Invalid backtest request")
            return client.submit_backtest({
                "strategy_ref": account, "dataset_refs": [dataset],
                "parameters": {}, **args,
            })
        if operation == "backtest_get":
            if set(args) != {"backtest_id"}:
                raise ValueError("Invalid backtest lookup")
            return client.get_backtest(args["backtest_id"])
        if operation == "backtest_cancel":
            if set(args) != {"backtest_id"}:
                raise ValueError("Invalid backtest cancellation")
            return client.cancel_backtest(args["backtest_id"])
    raise HTTPException(404, "Unknown research operation")
