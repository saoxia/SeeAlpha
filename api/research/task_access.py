"""Durable, task-bound Platform research capabilities; never Engine admin credentials.

Opt-in local development controller. SQLite transactions serialize budgets across
API workers sharing the same private file; no new service or public grant mint.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets
import sqlite3
import stat
import time
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException

from core.engine_contracts import EngineConflict

from ..market_data.repository import PolicyRepository
from ..tenant_docker import DockerTenantManager
from .candidates import parse_candidate
from .grants import ResearchGrantDenied, ResearchGrantIssuer
from .mcp_engine import EngineMCPResearchClient
from .python_candidates import parse_python_candidate

OPERATIONS = {
    "datasets": "datasets:read",
    "snapshot_create": "snapshots:create",
    "snapshot_get": "snapshots:read",
    "snapshot_fetch": "snapshots:read",
    "backtest_submit": "backtests:submit",
    "backtest_get": "backtests:read",
    "backtest_cancel": "backtests:cancel",
    # These remain Platform-owned, synchronous development research commands.
    # They never mint an Engine MCP grant or authorize venue order placement.
    "basket_run": "backtests:submit",
    "basket_latest": "backtests:read",
    "candidate_create": "candidates:submit",
    "candidate_get": "candidates:submit",
    "candidate_submit": "candidates:submit",
    "python_candidate_create": "python_candidates:submit",
    "python_candidate_submit": "python_candidates:submit",
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
                CREATE TABLE IF NOT EXISTS research_candidates (
                  id TEXT PRIMARY KEY, task_id TEXT NOT NULL,
                  owner_id INTEGER NOT NULL, tenant_id INTEGER NOT NULL,
                  account_id TEXT NOT NULL, dataset_ref TEXT NOT NULL,
                  turn INTEGER NOT NULL, name TEXT NOT NULL,
                  title TEXT NOT NULL, version TEXT NOT NULL,
                  package_hash TEXT NOT NULL, recipe TEXT NOT NULL,
                  archive BLOB NOT NULL, created_at INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS research_candidates_task
                  ON research_candidates(task_id, created_at);

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

    def create_candidate(self, lease: dict, archive: bytes, parsed: dict) -> dict:
        identity = uuid4().hex
        now = int(time.time())
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            live = db.execute(
                "SELECT * FROM research_leases WHERE task_id=?", (lease["task_id"],)
            ).fetchone()
            if (live is None or live["active"] != 1 or live["expires_at"] <= now
                    or live["turn"] != lease["turn"]
                    or live["owner_id"] != lease["owner_id"]
                    or live["tenant_id"] != lease["tenant_id"]
                    or live["dataset_ref"] != lease["dataset_ref"]):
                raise HTTPException(403, "Research candidate scope expired")
            count = db.execute(
                "SELECT COUNT(*) FROM research_candidates WHERE task_id=?",
                (lease["task_id"],),
            ).fetchone()[0]
            if count >= 24:
                raise HTTPException(429, "Research candidate version budget exhausted")
            db.execute(
                """INSERT INTO research_candidates
                (id,task_id,owner_id,tenant_id,account_id,dataset_ref,turn,
                 name,title,version,package_hash,recipe,archive,created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    identity, lease["task_id"], lease["owner_id"],
                    lease["tenant_id"], lease["account_id"], lease["dataset_ref"],
                    lease["turn"], parsed["name"], parsed["title"], parsed["version"],
                    parsed["sha256"], json.dumps(parsed["recipe"], sort_keys=True),
                    archive, now,
                ),
            )
            db.execute(
                "INSERT INTO research_audit(ts,task_id,event,detail) VALUES(?,?,?,?)",
                (now, lease["task_id"], "candidate_registered", identity),
            )
        return {
            "candidate_id": identity, "package_hash": parsed["sha256"],
            "name": parsed["name"], "title": parsed["title"],
            "version": parsed["version"], "recipe": parsed["recipe"],
        }

    def get_candidate(self, lease: dict, identity: str) -> dict:
        if (not isinstance(identity, str)
                or not re.fullmatch(r"[a-f0-9]{32}", identity)):
            raise HTTPException(404, "Research candidate not found")
        with self._connect() as db:
            row = db.execute(
                """SELECT * FROM research_candidates
                WHERE id=? AND task_id=? AND owner_id=? AND tenant_id=?
                  AND account_id=? AND dataset_ref=?""",
                (
                    identity, lease["task_id"], lease["owner_id"],
                    lease["tenant_id"], lease["account_id"], lease["dataset_ref"],
                ),
            ).fetchone()
        if row is None:
            raise HTTPException(404, "Research candidate not found")
        data = dict(row)
        data["recipe"] = json.loads(data["recipe"])
        return data

    def audit(self, event: dict) -> None:
        with self._connect() as db:
            db.execute(
                "INSERT INTO research_audit(ts,task_id,event,detail) VALUES(?,?,?,?)",
                (int(time.time()), event["task_id"], event["event"],
                 event.get("fingerprint", "")),
            )


def allowed_scope(app, owner_id: int, tenant_id: int, account_id: str,
                  dataset_ref: str, *, require_issued=True) -> bool:
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
        if not require_issued:
            return True
        loads = app.state.strategies.repository.list_issuances(tenant_id)
        selected = [row for row in loads if row["account_id"] == str(account_id)]
        return bool(selected) and all(
            row["purpose"] == "development"
            and row["parameters"].get("symbol") == symbol for row in selected
        )
    except Exception:  # noqa: BLE001 - any authority store failure must deny access
        return False


_BASKET_DEMOS = {
    "funding_carry": "demo_funding_carry",
    "protected_options": "demo_option_hedged_bollinger",
}


def _authorized_basket_scope(app, lease: dict, mode: str, interval: str) -> bool:
    """Require exactly the historical legs of an issued research Demo.

    A futures-only task lease is never blanket permission to access Spot or
    Options: every additional product must be enabled by the tenant policy.
    Resolve issuances and immutable package identities in the trusted database.
    """
    if mode not in _BASKET_DEMOS or interval not in {"1m", "5m"}:
        return False
    if not allowed_scope(
        app, lease["owner_id"], lease["tenant_id"], lease["account_id"],
        lease["dataset_ref"], require_issued=True,
    ):
        return False
    market, symbol, bound_interval = lease["dataset_ref"].split(":")
    if market != "futures_um" or interval != bound_interval:
        return False
    try:
        repo = app.state.strategies.repository
        selected = [
            item for item in repo.list_issuances(lease["tenant_id"])
            if item["account_id"] == str(lease["account_id"])
        ]
        expected = _BASKET_DEMOS[mode]
        if not selected or len(selected) > 16:
            return False
        options = set()
        for issued in selected:
            version = repo.get_version(issued["version_id"])
            package = repo.get_package(version["package_id"]) if version else None
            params = issued["parameters"]
            if (
                not isinstance(package, dict) or package.get("owner_id") is not None
                or package.get("code_key") != expected
                or issued["purpose"] != "development" or params.get("symbol") != symbol
            ):
                return False
            if mode == "protected_options":
                put, call = params.get("put_symbol"), params.get("call_symbol")
                root = symbol.removesuffix("USDT") + "-"
                if (
                    not isinstance(put, str) or not isinstance(call, str)
                    or not put.startswith(root) or not call.startswith(root)
                    or put == call or not put.endswith("-P") or not call.endswith("-C")
                    or len(put.split("-")) != 4 or len(call.split("-")) != 4
                    or put.split("-")[1] != call.split("-")[1]
                ):
                    return False
                options.update((put, call))
        if mode == "protected_options" and len(options) != 2:
            # A single coherent pair; no implicit cross-expiry or extra wings.
            return False
        policies = PolicyRepository(app.state.database).records(lease["tenant_id"])
        enabled = {
            (row["product"], row["symbol"])
            for row in policies if row["parameters"].get("history_enabled") is True
            and interval in row["parameters"].get("intervals", [])
        }
        required = (
            {("spot", symbol), ("futures_um", symbol)}
            if mode == "funding_carry"
            else {("futures_um", symbol)} | {("options", item) for item in options}
        )
        return required <= enabled
    except (KeyError, TypeError, AttributeError, ValueError, OSError):
        return False
    except Exception:  # noqa: BLE001 - any authority-store error denies basket access
        return False


def _basket_command(app, lease: dict, operation: str, args: dict) -> dict:
    """Direct to the existing Platform strategy service, not the Engine admin API.

    The task cannot supply tenant/account identifiers or create production
    grants. The service independently checks development-only capital bounds.
    """
    if not isinstance(args, dict):
        raise ValueError("Invalid basket research arguments")
    if operation == "basket_latest":
        if set(args) != {"mode"}:
            raise ValueError("Invalid basket research lookup")
        mode, interval = args["mode"], lease["dataset_ref"].rsplit(":", 1)[-1]
        if not _authorized_basket_scope(app, lease, mode, interval):
            raise HTTPException(403, "Basket research scope not authorized")
        return app.state.strategies.latest_basket_backtest(
            lease["tenant_id"], lease["account_id"], mode,
        )
    expected = {
        "mode", "start", "end", "interval", "starting_primary_balance",
        "starting_perpetual_balance", "max_gross_notional",
    }
    if operation != "basket_run" or set(args) != expected:
        raise ValueError("Invalid basket research command")
    mode, interval = args["mode"], args["interval"]
    if not _authorized_basket_scope(app, lease, mode, interval):
        raise HTTPException(403, "Basket research scope not authorized")
    try:
        # Synchronous 1-day bound: no unbounded historical replay through an AI
        # task or task-leased read while Engine is busy. Wider windows use UI.
        if (
            not isinstance(args["start"], str) or not isinstance(args["end"], str)
            or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", args["start"])
            or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", args["end"])
        ):
            raise ValueError("Invalid bounded basket date window")
        start = date.fromisoformat(args["start"])
        end = date.fromisoformat(args["end"])
    except (ValueError, TypeError, OverflowError):
        raise ValueError("Invalid bounded basket date window") from None
    if not 0 < (end - start).days <= 1:
        raise ValueError("Basket research requires a 1-day history window")
    for field in (
        "starting_primary_balance", "starting_perpetual_balance",
        "max_gross_notional",
    ):
        if not isinstance(args[field], str) or len(args[field]) > 40:
            raise ValueError("Invalid basket risk budget")
    return app.state.strategies.basket_backtest(
        lease["tenant_id"], lease["account_id"], mode,
        args["start"], args["end"], interval,
        args["starting_primary_balance"], args["starting_perpetual_balance"],
        args["max_gross_notional"],
    )


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
    """Authorize one candidate or fixed Engine operation; never run AI Python."""
    if operation in {"basket_run", "basket_latest"}:
        return _basket_command(app, lease, operation, args)
    require_issued = operation == "backtest_submit"
    if not allowed_scope(
        app, lease["owner_id"], lease["tenant_id"],
        lease["account_id"], lease["dataset_ref"], require_issued=require_issued,
    ):
        raise HTTPException(403, "Research scope no longer authorized")
    account = "account:" + lease["account_id"]
    dataset = lease["dataset_ref"]
    if operation == "candidate_create":
        if set(args) != {"archive"}:
            raise ValueError("Invalid candidate package request")
        archive, parsed = parse_candidate(args["archive"])
        if parsed["recipe"]["symbol"] != dataset.split(":")[1]:
            raise HTTPException(403, "Candidate symbol outside research scope")
        return app.state.research_leases.create_candidate(lease, archive, parsed)
    if operation == "python_candidate_create":
        if not app.state.settings.research_python_enabled:
            raise HTTPException(403, "Python candidate research is disabled")
        if set(args) != {"archive"}:
            raise ValueError("Invalid Python candidate package request")
        archive, parsed = parse_python_candidate(args["archive"])
        if parsed["recipe"]["parameters"]["symbol"] != dataset.split(":")[1]:
            raise HTTPException(403, "Python candidate symbol outside research scope")
        return app.state.research_leases.create_candidate(lease, archive, parsed)
    if operation in {"candidate_get", "candidate_submit", "python_candidate_submit"}:
        expected = {"candidate_id"} if operation == "candidate_get" else {
            "candidate_id", "start", "end", "starting_balance",
            "quote_currency", "idempotency_key",
        }
        if set(args) != expected:
            raise ValueError("Invalid candidate reference or backtest request")
        candidate = app.state.research_leases.get_candidate(lease, args["candidate_id"])
        is_python = candidate["recipe"].get("mode") == "python"
        if operation == "python_candidate_submit":
            if not app.state.settings.research_python_enabled or not is_python:
                raise HTTPException(403, "Python candidate not authorized")
        elif operation == "candidate_submit" and is_python:
            raise HTTPException(422, "Use Python candidate research command")
        if operation == "candidate_get":
            return {key: candidate[key] for key in (
                "candidate_id", "name", "title", "version", "package_hash", "recipe",
            ) if key in candidate} | {"candidate_id": candidate["id"]}
    else:
        candidate = None
    origin, key = engine_connection(app, lease["tenant_id"])
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
    with EngineMCPResearchClient(origin, grant) as client:
        if operation == "python_candidate_submit":
            return client.submit_python_candidate({
                "candidate_id": candidate["id"],
                "package_hash": candidate["package_hash"],
                "archive": base64.b64encode(candidate["archive"]).decode("ascii"),
                "strategy_ref": account, "dataset_refs": [dataset],
                "start": args["start"], "end": args["end"],
                "starting_balance": args["starting_balance"],
                "quote_currency": args["quote_currency"],
                "idempotency_key": args["idempotency_key"],
            })
        if operation == "candidate_submit":
            return client.submit_candidate({
                "candidate_id": candidate["id"],
                "package_hash": candidate["package_hash"],
                "archive": base64.b64encode(candidate["archive"]).decode("ascii"),
                "strategy_ref": account, "dataset_refs": [dataset],
                "start": args["start"], "end": args["end"],
                "starting_balance": args["starting_balance"],
                "quote_currency": args["quote_currency"],
                "idempotency_key": args["idempotency_key"],
            })
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
