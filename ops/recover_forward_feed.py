"""Explicit, audited resynchronization only across intervals without live paper quotes."""

import argparse
import fcntl
import json
import math
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from forward_validation.core import (
    DEFAULT_ROOT,
    SYMBOLS,
    connect,
    incident,
    meta,
    observe_trades,
    sha,
    verify,
    write_json,
)
from forward_validation.service import PublicAPI


def plan_resync(db, symbol, payload, now_ms):
    last_id, last_ts = meta(db, symbol + ":trade_id"), meta(db, symbol + ":trade_ts")
    if last_id is None or last_ts is None:
        raise ValueError("No prior cursor: use normal collector initialization")
    if not payload:
        raise ValueError("No fresh public trades")
    previous_id = previous_ts = None
    for trade in payload:
        identity, ts = int(trade["a"]), int(trade["T"])
        if (
            not isinstance(trade["m"], bool)
            or any(not math.isfinite(float(trade[k])) for k in ("p", "q"))
            or float(trade["p"]) <= 0
            or float(trade["q"]) < 0
            or ts > now_ms + 2000
            or (previous_id is not None and identity != previous_id + 1)
            or (previous_ts is not None and ts < previous_ts)
        ):
            raise ValueError("Malformed or discontinuous fresh trades")
        if "nq" in trade and (not math.isfinite(float(trade["nq"])) or float(trade["nq"]) < 0):
            raise ValueError("Malformed normal quantity")
        previous_id, previous_ts = identity, ts
    if now_ms - previous_ts > 15_000:
        raise ValueError("Replacement data is not current")
    unseen = [t for t in payload if int(t["a"]) > last_id]
    if not unseen:
        raise ValueError("Replacement data does not advance the existing cursor")
    first = unseen[0]
    if int(first["T"]) < last_ts:
        raise ValueError("Replacement trade time precedes the saved cursor")
    skipped = int(first["a"]) - last_id - 1
    protected = (
        db.execute(
            """SELECT COUNT(*) FROM quotes WHERE symbol=? AND prospective=1
        AND expires_ms>? AND active_ms<=?""",
            (symbol, last_ts, int(first["T"])),
        ).fetchone()[0]
        if skipped
        else 0
    )
    if protected:
        raise ValueError(
            f"{symbol}: {protected} prospective quotes overlap the gap; retain normal replay"
        )
    return {
        "symbol": symbol,
        "previous_id": last_id,
        "previous_ms": last_ts,
        "first_id": int(first["a"]),
        "first_ms": int(first["T"]),
        "new_id": int(unseen[-1]["a"]),
        "new_ms": int(unseen[-1]["T"]),
        "skipped_trade_ids": skipped,
        "protected_quotes": protected,
        "reason": "Only intervals without prospective quotes may be skipped; missed decisions stay missed",
    }


def apply_batches(db, batches, now_ms):
    """All markets validate before any mutation; gap flags remain visible to analysis."""
    db.execute("BEGIN IMMEDIATE")
    try:
        plans = [plan_resync(db, symbol, payload, now_ms) for symbol, payload in batches.items()]
        for plan in plans:
            symbol = plan["symbol"]
            observe_trades(db, symbol, batches[symbol])
            incident(
                db, now_ms, symbol, "operational_feed_resync", json.dumps(plan, sort_keys=True)
            )
        db.commit()
    except Exception:
        db.rollback()
        raise
    return plans


def recover(root, *, apply=False):
    root = Path(root)
    manifest = verify(root)
    # The collector holds this lock for its complete lifetime, including API calls.
    with (root / "collector.lock").open("a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("Stop the collector before attempting feed recovery") from exc
        with closing(connect(root)) as db:
            api = PublicAPI(root, db)
            api.sync_clock()
            if not api.healthy:
                raise RuntimeError("Clock check failed")
            batches = {
                symbol: api.get("aggTrades", symbol=symbol, limit=1000) for symbol in SYMBOLS
            }
            now = api.now()
            plans = [plan_resync(db, symbol, payload, now) for symbol, payload in batches.items()]
            if not apply:
                return {"applied": False, "plans": plans}
            directory = root / "maintenance" / datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
            directory.mkdir(parents=True)
            with closing(sqlite3.connect(directory / "before.sqlite")) as backup:
                db.backup(backup)
            record = {
                "applied": False,
                "plans": plans,
                "manifest_sha256": sha(root / "manifest.json"),
                "recovery_source_sha256": sha(__file__),
                "study_start_ms": manifest["start_ms"],
                "study_end_ms": manifest["end_ms"],
                "created_ms": now,
                "backup": str(directory / "before.sqlite"),
            }
            write_json(directory / "plan.json", record)
            applied = apply_batches(db, batches, now)
            record.update(applied=True, plans=applied)
            write_json(directory / "result.json", record)
            return {**record, "audit_directory": str(directory)}


def main():
    parser = argparse.ArgumentParser(
        description="仅在积压区间没有有效模拟报价时恢复实时成交；保留缺口及备份"
    )
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument(
        "--apply", action="store_true", help="实际推进游标；省略时仅保存公开响应并预览方案"
    )
    args = parser.parse_args()
    print(json.dumps(recover(args.root, apply=args.apply), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
