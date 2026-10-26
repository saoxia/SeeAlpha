"""Causal signal observations and durable storage for the frozen forward study."""

import gzip
import hashlib
import importlib.metadata
import json
import math
import os
import sqlite3
import statistics
import sys
from collections import deque
from datetime import UTC, datetime
from decimal import ROUND_DOWN, ROUND_HALF_EVEN, Decimal
from itertools import pairwise
from pathlib import Path

from strategy_python._indicator.bars import CompletedBarAggregator
from strategy_python._mechanism.rules import mean_reversion_snapshot
from strategy_python.btc_cycle_optimized_20260915_mrl_original.indicators import (
    RollingBollinger,
    RollingMean,
)

PROJECT = Path(__file__).resolve().parents[1]
DEFAULT_ROOT = PROJECT / "data/forward_validation/btc_eth_mrl_20260921"
MINUTE = 60_000
HOUR = 60 * MINUTE
DAY = 24 * HOUR
SYMBOLS = ("BTCUSDT", "ETHUSDT")
HORIZONS = (1, 4, 24)
PARAMETERS = {
    "boll_period": 20,
    "boll_std": 2.0,
    "ma_period": 99,
    "ma_window": 54,
    "ma_max_ratio": 0.65,
    "maker_fee": 0.0002,
    "taker_fee": 0.0005,
    "extra_fee": 0.0005,
    "notional": "200",
    "latency_ms": 1000,
    "decision_deadline_ms": 15_000,
    "study_days": 90,
    "minimum_events": 300,
    "minimum_days": 60,
    "minimum_coverage": 0.95,
    "minimum_matched": 0.90,
    "seed": 20260921,
}


def utc(ms):
    return datetime.fromtimestamp(ms / 1000, UTC).isoformat()


def sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def json_bytes(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False).encode()


def write_json(path, value):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("wb") as f:
        f.write(json_bytes(value) + b"\n")
        f.flush()
        os.fsync(f.fileno())
    temporary.replace(path)


def connect(root):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(root / "observations.sqlite", timeout=30)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA synchronous=FULL")
    db.executescript("""
        CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS bars(
            symbol TEXT, end_ms INTEGER, open REAL, high REAL, low REAL, close REAL,
            received_ms INTEGER, PRIMARY KEY(symbol,end_ms));
        CREATE TABLE IF NOT EXISTS quotes(
            symbol TEXT, decision_ms INTEGER, recorded_ms INTEGER, active_ms INTEGER,
            expires_ms INTEGER, price TEXT, tick TEXT, quantity TEXT, filter_ok INTEGER,
            prospective INTEGER, status TEXT, vol REAL, armed INTEGER DEFAULT 0,
            gapped INTEGER DEFAULT 0, PRIMARY KEY(symbol,decision_ms));
        CREATE TABLE IF NOT EXISTS touches(
            symbol TEXT, decision_ms INTEGER, kind TEXT, ts INTEGER, trade_id INTEGER,
            evidence TEXT, PRIMARY KEY(symbol,decision_ms,kind));
        CREATE TABLE IF NOT EXISTS funding(
            symbol TEXT, ts INTEGER, rate_type TEXT, rate TEXT, mark TEXT,
            PRIMARY KEY(symbol,ts,rate_type));
        CREATE TABLE IF NOT EXISTS outcomes(
            symbol TEXT, decision_ms INTEGER, kind TEXT, horizon INTEGER, anchor_ms INTEGER,
            exit_ms INTEGER, gross_bp REAL, intraminute_bp REAL, after_close_bp REAL,
            funding_bp REAL, fee_bp REAL, net_bp REAL, stress_bp REAL, mfe_bp REAL,
            mae_bp REAL, status TEXT, PRIMARY KEY(symbol,decision_ms,kind,horizon));
        CREATE TABLE IF NOT EXISTS incidents(ts INTEGER, symbol TEXT, kind TEXT, detail TEXT);
        CREATE TABLE IF NOT EXISTS requests(
            id INTEGER PRIMARY KEY, received_ms INTEGER, endpoint TEXT, parameters TEXT,
            path TEXT, payload_sha256 TEXT, chain_sha256 TEXT);
        CREATE INDEX IF NOT EXISTS touches_time ON touches(symbol,ts);
        CREATE INDEX IF NOT EXISTS quotes_active ON quotes(symbol,active_ms,expires_ms);
    """)
    return db


def meta(db, key, default=None):
    row = db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return json.loads(row[0]) if row else default


def set_meta(db, key, value):
    db.execute(
        "INSERT INTO meta VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, json_bytes(value).decode()),
    )


def incident(db, now, symbol, kind, detail):
    db.execute("INSERT INTO incidents VALUES(?,?,?,?)", (now, symbol, kind, str(detail)))


def archive_response(root, db, endpoint, parameters, payload, received_ms):
    directory = Path(root) / "raw"
    directory.mkdir(exist_ok=True)
    path = directory / (utc(received_ms)[:10] + ".jsonl.gz")
    record = json_bytes(
        {
            "received_ms": received_ms,
            "endpoint": endpoint,
            "parameters": parameters,
            "payload": payload,
        }
    )
    digest = hashlib.sha256(record).hexdigest()
    with path.open("ab") as file:
        with gzip.GzipFile(fileobj=file, mode="wb", mtime=0) as compressed:
            compressed.write(record + b"\n")
        file.flush()
        os.fsync(file.fileno())
    with db:
        previous = meta(db, "request_chain", "")
        chain = hashlib.sha256((previous + digest).encode()).hexdigest()
        db.execute(
            "INSERT INTO requests(received_ms,endpoint,parameters,path,payload_sha256,chain_sha256) VALUES(?,?,?,?,?,?)",
            (
                received_ms,
                endpoint,
                json_bytes(parameters).decode(),
                str(path.relative_to(root)),
                digest,
                chain,
            ),
        )
        set_meta(db, "request_chain", chain)


class SignalState:
    """Identical market feature formulas/order; no account or execution state."""

    def __init__(self):
        self.five = CompletedBarAggregator(5)
        self.fifteen = CompletedBarAggregator(15)
        self.boll = RollingBollinger(20, 2.0)
        self.ma = RollingMean(99)
        self.adverse = deque(maxlen=54)
        self.closes = deque(maxlen=1441)
        self.times = deque(maxlen=1441)

    def update(self, end_ms, open_, high, low, close):
        if self.times and end_ms != self.times[-1] + MINUTE:
            # A missing minute invalidates feature history until a fresh full warmup.
            self.__init__()
        ts = (int(end_ms) - 1) * 1_000_000
        five = self.five.update(ts, open_, high, low, close)
        fifteen = self.fifteen.update(ts, open_, high, low, close)
        self.closes.append(close)
        self.times.append(int(end_ms))
        signal = None
        if five is not None:
            bands = self.boll.update(five.close)
            if bands is not None:
                lower, middle, _ = bands
                snapshot = mean_reversion_snapshot(
                    lower,
                    middle,
                    (0.30458469520231835, 0.9536560054231893, 2.3278801433036),
                    0.0004,
                )
                ratio = sum(self.adverse) / 54 if len(self.adverse) == 54 else None
                vol = None
                if len(self.closes) == 1441 and self.times[-1] - self.times[0] == DAY:
                    values = list(self.closes)
                    vol = statistics.pstdev(math.log(b / a) for a, b in pairwise(values))
                signal = {
                    "lower": lower,
                    "middle": middle,
                    "close": five.close,
                    "ratio": ratio,
                    "vol": vol,
                    "valid": snapshot is not None,
                    "filter_ok": ratio is not None and ratio <= 0.65 + 1e-12,
                }
        # Original strategy evaluates 5m before updating same-boundary 15m features.
        if fifteen is not None:
            ma = self.ma.update(fifteen.close)
            if ma is not None:
                self.adverse.append(fifteen.low < ma)
        return signal


def make_quote(symbol, decision_ms, signal, now_ms, rules, manifest, healthy):
    tick, step = Decimal(rules["tick"]), Decimal(rules["step"])
    price = (Decimal(str(signal["lower"])) / tick).to_integral_value(
        rounding=ROUND_HALF_EVEN
    ) * tick
    quantity = (
        (Decimal(PARAMETERS["notional"]) / price / step).to_integral_value(rounding=ROUND_DOWN)
        * step
        if price > 0
        else Decimal(0)
    )
    status = "ready"
    if not signal["valid"] or signal["ratio"] is None or not signal["vol"]:
        status = "warmup_or_invalid"
    elif not manifest["start_ms"] <= decision_ms < manifest["end_ms"]:
        status = "outside_study"
    elif now_ms < decision_ms or now_ms - decision_ms > PARAMETERS["decision_deadline_ms"]:
        status = "missed_late"
    elif not healthy:
        status = "feed_not_ready"
    elif (
        quantity < Decimal(rules["min_qty"])
        or quantity > Decimal(rules["max_qty"])
        or quantity * price < Decimal(rules["min_notional"])
    ):
        status = "order_constraint"
    elif price >= Decimal(str(signal["close"])):
        status = "marketable_at_decision"
    return {
        "symbol": symbol,
        "decision_ms": int(decision_ms),
        "recorded_ms": int(now_ms),
        "active_ms": int(now_ms) + PARAMETERS["latency_ms"],
        "expires_ms": int(decision_ms) + 5 * MINUTE,
        "price": str(price),
        "tick": str(tick),
        "quantity": str(quantity),
        "filter_ok": int(signal["filter_ok"]),
        "prospective": int(status == "ready"),
        "status": status,
        "vol": signal["vol"],
    }


def save_quote(db, quote):
    columns = list(quote)
    db.execute(
        f"INSERT OR IGNORE INTO quotes({','.join(columns)}) VALUES({','.join('?' for _ in columns)})",
        tuple(quote.values()),
    )


def observe_trades(db, symbol, trades):
    """Trade IDs/cursor and touch insertion commit together; retries are idempotent."""
    last = meta(db, symbol + ":trade_id")
    last_ts = meta(db, symbol + ":trade_ts")
    quotes = [
        dict(q)
        for q in db.execute(
            "SELECT * FROM quotes WHERE symbol=? AND prospective=1 AND gapped=0 AND expires_ms>? ORDER BY decision_ms",
            (symbol, last_ts or 0),
        )
    ]
    existing = {
        (r["decision_ms"], r["kind"])
        for r in db.execute("SELECT decision_ms,kind FROM touches WHERE symbol=?", (symbol,))
    }
    for trade in sorted(trades, key=lambda x: int(x["a"])):
        identity, ts = int(trade["a"]), int(trade["T"])
        if last is not None and identity <= last:
            continue
        if last is not None and (identity != last + 1 or ts < last_ts):
            start, end = min(last_ts, ts), max(last_ts, ts)
            incident(
                db,
                ts,
                symbol,
                "trade_gap",
                {"previous_id": last, "next_id": identity, "start_ms": start, "end_ms": end},
            )
            db.execute(
                "UPDATE quotes SET gapped=1 WHERE symbol=? AND active_ms<=? AND expires_ms>=?",
                (symbol, end, start),
            )
            for q in quotes:
                if q["active_ms"] <= end and q["expires_ms"] >= start:
                    q["gapped"] = 1
        price = Decimal(trade["p"])
        for q in quotes:
            if q["gapped"] or not q["active_ms"] <= ts < q["expires_ms"]:
                continue
            limit = Decimal(q["price"])
            if q["armed"] == 0:
                q["armed"] = 1 if price > limit else -1
                db.execute(
                    "UPDATE quotes SET armed=?,status=? WHERE symbol=? AND decision_ms=?",
                    (
                        q["armed"],
                        "ready" if q["armed"] == 1 else "marketable_uncertain",
                        symbol,
                        q["decision_ms"],
                    ),
                )
            if q["armed"] != 1:
                continue
            kinds = []
            if price <= limit:
                kinds.append("broad")
            if (
                trade["m"] is True
                and price <= limit - Decimal(q["tick"])
                and Decimal(trade.get("nq", trade["q"])) >= Decimal(q["quantity"])
            ):
                kinds.append("strict")
            for kind in kinds:
                key = (q["decision_ms"], kind)
                if key not in existing:
                    db.execute(
                        "INSERT OR IGNORE INTO touches VALUES(?,?,?,?,?,?)",
                        (symbol, q["decision_ms"], kind, ts, identity, json_bytes(trade).decode()),
                    )
                    existing.add(key)
        last, last_ts = identity, ts
    if last is not None:
        set_meta(db, symbol + ":trade_id", last)
        set_meta(db, symbol + ":trade_ts", last_ts)


def settle(db, symbol, now_ms):
    coverage = meta(db, symbol + ":funding_covered_ms", 0)
    pending = db.execute(
        """SELECT t.*,q.price FROM touches t JOIN quotes q USING(symbol,decision_ms)
        WHERE t.symbol=? AND (SELECT COUNT(*) FROM outcomes o WHERE o.symbol=t.symbol
        AND o.decision_ms=t.decision_ms AND o.kind=t.kind)<3""",
        (symbol,),
    ).fetchall()
    for touch in pending:
        anchor = (touch["ts"] // MINUTE + 1) * MINUTE
        price = float(touch["price"])
        for h in HORIZONS:
            end = anchor + h * HOUR
            if end > min(coverage, now_ms - MINUTE):
                continue
            if db.execute(
                "SELECT 1 FROM outcomes WHERE symbol=? AND decision_ms=? AND kind=? AND horizon=?",
                (symbol, touch["decision_ms"], touch["kind"], h),
            ).fetchone():
                continue
            bars = db.execute(
                "SELECT * FROM bars WHERE symbol=? AND end_ms BETWEEN ? AND ? ORDER BY end_ms",
                (symbol, anchor, end),
            ).fetchall()
            complete = (
                len(bars) == h * 60 + 1
                and bars[0]["end_ms"] == anchor
                and bars[-1]["end_ms"] == end
            )
            if not complete:
                db.execute(
                    "INSERT INTO outcomes(symbol,decision_ms,kind,horizon,anchor_ms,exit_ms,status) VALUES(?,?,?,?,?,?,?)",
                    (symbol, touch["decision_ms"], touch["kind"], h, anchor, end, "missing_bars"),
                )
                continue
            exit_price, close = bars[-1]["close"], bars[0]["close"]
            funding = db.execute(
                "SELECT rate,mark FROM funding WHERE symbol=? AND ts>? AND ts<=?",
                (symbol, touch["ts"], end),
            ).fetchall()
            funding_bp = sum(float(x["rate"]) * float(x["mark"]) / price * 1e4 for x in funding)
            gross = (exit_price / price - 1) * 1e4
            fees = (PARAMETERS["maker_fee"] + PARAMETERS["taker_fee"] * exit_price / price) * 1e4
            stress = PARAMETERS["extra_fee"] * (1 + exit_price / price) * 1e4
            # Future minute extrema exclude the touch minute; cash returns include it.
            mfe = max(0, max(b["high"] for b in bars[1:]) / price - 1) * 1e4
            mae = min(0, min(b["low"] for b in bars[1:]) / price - 1) * 1e4
            db.execute(
                "INSERT INTO outcomes VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    symbol,
                    touch["decision_ms"],
                    touch["kind"],
                    h,
                    anchor,
                    end,
                    gross,
                    (close / price - 1) * 1e4,
                    (exit_price / close - 1) * 1e4,
                    funding_bp,
                    fees,
                    gross - fees - funding_bp,
                    gross - fees - funding_bp - stress,
                    mfe,
                    mae,
                    "complete",
                ),
            )


def source_files():
    files = list((PROJECT / "forward_validation").glob("*.py"))
    files += [
        PROJECT / "forward_validation/PROTOCOL.md",
    ]
    for package in (
        "btc_cycle_optimized_20260915_mrl_original",
        "_indicator",
        "_mechanism",
        "_risk",
        "_model",
        "_execution",
    ):
        files.extend((PROJECT / "strategy_python" / package).rglob("*.py"))
    return sorted(files)


def verify(root):
    root = Path(root)
    archive = json.loads((root / "archive.json").read_text())
    if sha(root / "manifest.json") != archive["manifest_sha256"]:
        raise RuntimeError("Frozen manifest changed")
    if sha(root / "frozen_sources.zip") != archive["sha256"]:
        raise RuntimeError("Frozen source archive changed")
    manifest = json.loads((Path(root) / "manifest.json").read_text())
    if manifest["parameters"] != PARAMETERS:
        raise RuntimeError("Forward parameters changed")
    if set(manifest["source_sha256"]) != {str(p.relative_to(PROJECT)) for p in source_files()}:
        raise RuntimeError("Frozen source inventory changed")
    for path, expected in manifest["source_sha256"].items():
        if sha(PROJECT / path) != expected:
            raise RuntimeError("Frozen source changed: " + path)
    environment = manifest["environment"]
    if environment["python"] != sys.version or any(
        importlib.metadata.version(name) != version
        for name, version in environment["packages"].items()
    ):
        raise RuntimeError("Frozen Python/package environment changed")
    return manifest
