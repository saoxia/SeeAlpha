"""Managed pair evidence journal, separate from the closed-minute trading feed."""

import fcntl
import json
import sqlite3
from contextlib import contextmanager
from decimal import Decimal, InvalidOperation
from itertools import pairwise

from core.market.instruments import INTERVALS, bar_window
from core.market.models import DataError


def _filtered_zero_trade(detail):
    """Recognize zero receipts, including evidence recorded by older collectors."""
    try:
        value = json.loads(detail)
        if not isinstance(value, dict) or value.get("kind") not in {"invalid_raw_trade", "filtered_zero_trade"}:
            return None
        row = value["row"]
        price, size = Decimal(str(row["price"])), Decimal(str(row["qty"]))
        if (type(row["id"]) is int and row["id"] >= 0 and type(row["time"]) is int and row["time"] >= 0
                and type(row["isBuyerMaker"]) is bool and price.is_finite() and size.is_finite()
                and price >= 0 and size >= 0 and (price == 0 or size == 0)):
            return row
    except (ValueError, TypeError, KeyError, InvalidOperation):
        pass
    return None


@contextmanager
def journal_access(root):
    """Serialize short journal mutations across collection and calculation processes."""
    with (root / "journal.lock").open("a") as lease:
        fcntl.flock(lease, fcntl.LOCK_EX)
        yield


class PairStore:
    def __init__(self, path, symbol, base_level, intervals=()):
        self.symbol, self.base_level = symbol, base_level
        self.retained = set(intervals) | ({base_level} if base_level != "tick" else set())
        if self.retained - set(INTERVALS):
            raise ValueError("Unsupported candle interval")
        self.forming = {}
        self.db = sqlite3.connect(path)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS config (symbol TEXT, base_level TEXT);
            CREATE TABLE IF NOT EXISTS candles (
                interval TEXT, start_ms INTEGER, end_ms INTEGER, value TEXT,
                PRIMARY KEY(interval,start_ms));
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY, timestamp_ms INTEGER, price TEXT, size TEXT, maker INTEGER);
            -- Retain the legacy schema and records; ID continuity is no longer checked.
            CREATE TABLE IF NOT EXISTS gaps (first_id INTEGER, last_id INTEGER, start_ms INTEGER, end_ms INTEGER,
                PRIMARY KEY(first_id,last_id));
            CREATE TABLE IF NOT EXISTS conflicts (detail TEXT PRIMARY KEY);
            CREATE INDEX IF NOT EXISTS trades_by_time ON trades(timestamp_ms,id);
            CREATE TABLE IF NOT EXISTS revisions (id INTEGER PRIMARY KEY, value INTEGER);
            CREATE TABLE IF NOT EXISTS runtime_values (name TEXT PRIMARY KEY, value INTEGER);
            INSERT OR IGNORE INTO revisions VALUES (1,0);
            CREATE TABLE IF NOT EXISTS pending_updates (
                source TEXT, start_ms INTEGER, PRIMARY KEY(source,start_ms));
            CREATE TRIGGER IF NOT EXISTS changed_trade AFTER INSERT ON trades BEGIN
                INSERT OR IGNORE INTO pending_updates VALUES ('tick',NEW.timestamp_ms/1000*1000);
            END;
            CREATE TRIGGER IF NOT EXISTS changed_candle AFTER INSERT ON candles BEGIN
                INSERT OR IGNORE INTO pending_updates VALUES (NEW.interval,NEW.start_ms);
            END;
            CREATE TRIGGER IF NOT EXISTS revised_candle AFTER UPDATE OF value ON candles BEGIN
                INSERT OR IGNORE INTO pending_updates VALUES (NEW.interval,NEW.start_ms);
            END;
        """)
        old = self.db.execute("SELECT * FROM config").fetchone()
        if old and old != (symbol, base_level):
            raise DataError("Pair configuration differs from journal")
        with self.db:
            if not old:
                self.db.execute("INSERT INTO config VALUES (?,?)", (symbol, base_level))
        self.last_id = self.db.execute("SELECT max(id) FROM trades").fetchone()[0]
        started = self.db.execute("SELECT value FROM runtime_values WHERE name='started_ms'").fetchone()
        self.started_ms = started[0] if started else None
        self.latest = {}
        self.revision = max(self.db.execute("SELECT coalesce(max(json_extract(value, '$.revision')),0) FROM candles").fetchone()[0],
                            self.db.execute("SELECT value FROM revisions WHERE id=1").fetchone()[0])
        # Recover the bounded working tail even if the previous process stopped
        # after acknowledging an in-memory forming candle.
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO pending_updates SELECT 'tick',timestamp_ms/1000*1000 FROM trades WHERE timestamp_ms >= (SELECT max(timestamp_ms)-120000 FROM trades)")
        for interval in self.retained:
            record = self.db.execute("SELECT value FROM candles WHERE interval=? AND coalesce(json_extract(value,'$.retained'),1)=1 ORDER BY start_ms DESC LIMIT 1", (interval,)).fetchone()
            if record:
                self.latest[interval] = json.loads(record[0])

    @property
    def conflicted(self):
        return any(_filtered_zero_trade(detail) is None for (detail,) in self.db.execute("SELECT detail FROM conflicts"))

    def candle(self, interval, row, now_ms, *, complete=True, source="binance_klines", retained=True):
        left, right = int(row[0]), int(row[6]) + 1
        if bar_window(interval, left) != (left, right):
            raise DataError("Unexpected exchange candle boundary")
        values = [Decimal(str(row[i])) for i in range(1, 6)]
        opening, high, low, close, volume = values
        if (not all(value.is_finite() for value in values) or min(values[:4]) <= 0 or volume < 0
                or high < max(opening, close) or low > min(opening, close) or low > high):
            raise DataError("Invalid exchange candle values")
        value = {"symbol": self.symbol, "interval": interval, "start_ms": left, "end_ms": right,
                 "open": str(opening), "high": str(high), "low": str(low), "close": str(close), "volume": str(volume),
                 "is_closed": right <= now_ms, "is_complete": complete and right <= now_ms,
                 "updated_ms": now_ms, "allow_new_risk": False, "source_levels": [source], "retained": retained}
        old = self.db.execute("SELECT value FROM candles WHERE interval=? AND start_ms=?", (interval, left)).fetchone()
        previous = json.loads(old[0]) if old else self.forming.get((interval, left))
        if previous:
            # Unselecting a period affects future retention, never erases datasets
            # that were already promised for retention by an earlier configuration.
            retained = retained or previous.get("retained", True)
            value["retained"] = retained
            differs = any(Decimal(previous[k]) != Decimal(value[k]) for k in ("open", "high", "low", "close", "volume"))
            # Recompute derived candles from their de-duplicated source; authoritative
            # venue closes and conflicting raw identities retain their original evidence.
            derived = (source in {"raw_trades", "1s", "1m"} and len(previous["source_levels"]) == 1
                       and previous["source_levels"][0] in {"raw_trades", "1s", "1m"})
            if previous["is_complete"] and not value["is_complete"] and (not derived or not differs):
                prior = self.latest.get(interval)
                if retained and interval in self.retained and (prior is None or left >= prior["start_ms"]):
                    self.latest[interval] = previous
                return previous
            if previous["is_complete"] and differs and not derived:
                with self.db:
                    self.db.execute("INSERT OR IGNORE INTO conflicts VALUES (?)", (json.dumps(value),))
                raise DataError("Conflicting closed candle; preserved original")
            if (not differs and all(previous.get(k, True if k == "retained" else None) == value[k]
                                   for k in ("is_closed", "is_complete", "source_levels", "retained"))):
                if retained and interval in self.retained:
                    prior = self.latest.get(interval)
                    if prior is None or left >= prior["start_ms"]:
                        self.latest[interval] = previous
                return previous
        self.revision += 1
        with self.db:
            self.db.execute("UPDATE revisions SET value=? WHERE id=1", (self.revision,))
        value["revision"] = self.revision
        if value["is_closed"]:
            with self.db:
                self.db.execute("INSERT OR REPLACE INTO candles VALUES (?,?,?,?)", (interval, left, right, json.dumps(value)))
            self.forming.pop((interval, left), None)
        else:
            self.forming[(interval, left)] = value
            with self.db:
                self.db.execute("INSERT OR IGNORE INTO pending_updates VALUES (?,?)", (interval, left))
        prior = self.latest.get(interval)
        if retained and interval in self.retained and (prior is None or left >= prior["start_ms"]):
            self.latest[interval] = value
        return value

    def ticks(self, rows, now_ms, *, generate_seconds=False, quarantine_invalid=False, calculate=True):
        parsed = []
        for row in rows:
            identity, stamp = row["id"], row["time"]
            price, size = Decimal(str(row["price"])), Decimal(str(row["qty"]))
            if (type(identity) is not int or identity < 0 or type(stamp) is not int or stamp < 0 or stamp > now_ms + 2000
                    or not price.is_finite() or not size.is_finite() or price < 0 or size < 0
                    or type(row["isBuyerMaker"]) is not bool):
                raise DataError("Invalid raw trade")
            if price == 0 or size == 0:
                if not quarantine_invalid:
                    raise DataError("Invalid raw trade")
                # Zero events are normal filtered receipts, not market trades or
                # missing IDs. Keep the filter receipt in the existing evidence journal.
                with self.db:
                    inserted = self.db.execute("INSERT OR IGNORE INTO conflicts VALUES (?)", (json.dumps({"kind": "filtered_zero_trade", "row": row}),))
                if inserted.rowcount:
                    print(f"Filtered zero trade: {json.dumps(row)}", flush=True)
                continue
            parsed.append((identity, stamp, str(price), str(size), int(row["isBuyerMaker"])))
        parsed.sort()
        seen = {}
        for item in parsed:
            old = seen.get(item[0]) or self.db.execute("SELECT * FROM trades WHERE id=?", (item[0],)).fetchone()
            seen[item[0]] = item
            if old and (old[:2] != item[:2] or Decimal(old[2]) != Decimal(item[2]) or Decimal(old[3]) != Decimal(item[3]) or old[4] != item[4]):
                with self.db:
                    self.db.execute("INSERT OR IGNORE INTO conflicts VALUES (?)", (json.dumps(item),))
                raise DataError("Conflicting raw trade")
        if self.started_ms is None:
            self.started_ms = now_ms
            with self.db:
                self.db.execute("INSERT OR IGNORE INTO runtime_values VALUES ('started_ms',?)", (now_ms,))
        with self.db:
            previous = self.last_id
            for item in parsed:
                self.db.execute("INSERT OR IGNORE INTO trades VALUES (?,?,?,?,?)", item)
                previous = max(previous if previous is not None else item[0], item[0])
            self.last_id = previous
        if generate_seconds:
            self.retained.add("1s")
        # A new event also revisits the previous source second so that its close is
        # data-triggered. Repeated pages do not create new derived revisions.
        if parsed:
            prior = self.db.execute("SELECT max(timestamp_ms)/1000*1000 FROM trades WHERE timestamp_ms<?",
                                    (max(item[1] for item in parsed) // 1000 * 1000,)).fetchone()[0]
            if prior is not None:
                with self.db:
                    self.db.execute("INSERT OR IGNORE INTO pending_updates VALUES ('tick',?)", (prior,))
        if calculate:
            self.process_changes(now_ms)
        return parsed

    def process_changes(self, now_ms):
        """Acknowledge dirty buckets only after their downstream writes succeed."""
        started = self.db.execute("SELECT value FROM runtime_values WHERE name='started_ms'").fetchone()
        self.started_ms = started[0] if started else self.started_ms
        while row := self.db.execute("""SELECT source,start_ms FROM pending_updates
                ORDER BY CASE source WHEN 'tick' THEN 0 WHEN '1s' THEN 1 WHEN '1m' THEN 2 ELSE 3 END,start_ms LIMIT 1""").fetchone():
            source, stamp = row
            if source == "tick" and self.retained:
                # New managed policies use official minutes directly. Explicit
                # legacy second retention keeps its public CLI behaviour only.
                target = "1s" if "1s" in self.retained else "1m"
                left, right = bar_window(target, stamp)
                trades = self.db.execute("SELECT timestamp_ms,price,size FROM trades WHERE timestamp_ms>=? AND timestamp_ms<? ORDER BY timestamp_ms,id",
                                         (left, right)).fetchall()
                if trades:
                    prices = [Decimal(value[1]) for value in trades]
                    complete = (self.started_ms is not None and left >= self.started_ms
                                and (target == "1s" or len({value[0] // 1000 for value in trades}) == 60))
                    self.candle(target, [left, str(prices[0]), str(max(prices)), str(min(prices)), str(prices[-1]),
                                str(sum((Decimal(value[2]) for value in trades), Decimal(0))), right - 1],
                                now_ms, complete=complete, source="raw_trades", retained=target in self.retained)
                with self.db:
                    self.db.execute("DELETE FROM pending_updates WHERE source='tick' AND start_ms>=? AND start_ms<?", (left, right))
            elif source == "1s" and self.retained - {"1s"}:
                self._rollup("1s", "1m", stamp, now_ms)
            elif source == "1m":
                for interval in INTERVALS:
                    if interval in self.retained - {"1s", "1m"}:
                        self._rollup("1m", interval, stamp, now_ms)
            with self.db:
                self.db.execute("DELETE FROM pending_updates WHERE source=? AND start_ms=?", row)
        # Internal seconds/minutes are bounded working inputs, not retained datasets.
        before = min((bar_window(level, max(0, bar_window(level, now_ms)[0] - 1))[0]
                      for level in self.retained - {"1s", "1m"}), default=now_ms - 120000)
        with self.db:
            if self.base_level != "tick":
                self.db.execute("DELETE FROM trades WHERE timestamp_ms<?", (now_ms - 120000,))
            for interval, cutoff in (("1s", now_ms - 120000), ("1m", before)):
                self.db.execute("DELETE FROM candles WHERE interval=? AND end_ms<? AND json_extract(value,'$.retained')=0",
                                (interval, cutoff))

    def _rollup(self, source, target, stamp, now_ms):
        left, right = bar_window(target, stamp)
        rows = {value["start_ms"]: value for record in self.db.execute(
            "SELECT value FROM candles WHERE interval=? AND start_ms>=? AND end_ms<=? ORDER BY start_ms",
            (source, left, right)) if (value := json.loads(record[0]))}
        rows.update({start: value for (interval, start), value in self.forming.items()
                     if interval == source and left <= start < right})
        values = sorted(rows.values(), key=lambda value: value["start_ms"])
        if not values:
            return
        complete = (values[0]["start_ms"] == left and values[-1]["end_ms"] == right
                    and all(value["is_complete"] for value in values)
                    and all(a["end_ms"] == b["start_ms"] for a, b in pairwise(values)))
        self.candle(target, [left, values[0]["open"],
                    str(max(Decimal(value["high"]) for value in values)),
                    str(min(Decimal(value["low"]) for value in values)), values[-1]["close"],
                    str(sum((Decimal(value["volume"]) for value in values), Decimal(0))), right - 1],
                    now_ms, complete=complete, source=source, retained=target in self.retained)

    def close(self):
        self.db.close()


def journal_inventory(path, symbol):
    """Read-only management projection; absence never creates a replacement journal."""
    if not path.is_file():
        return []
    db = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
    try:
        result = []
        for level, first, last in db.execute("SELECT interval,min(start_ms),max(end_ms) FROM candles WHERE coalesce(json_extract(value,'$.retained'),1)=1 GROUP BY interval"):
            result.append({"symbol": symbol, "product": "futures_um", "level": level,
                           "event_ranges_ns": [[str(first * 1000000), str((last - 1) * 1000000)]], "quality": "unverified"})
        config = db.execute("SELECT base_level FROM config").fetchone()
        first, last = db.execute("SELECT min(timestamp_ms),max(timestamp_ms) FROM trades").fetchone()
        if config and config[0] == "tick" and first is not None:
            result.append({"symbol": symbol, "product": "futures_um", "level": "tick",
                           "event_ranges_ns": [[str(first * 1000000), str(last * 1000000)]], "quality": "partial_recent_trades"})
        return result
    finally:
        db.close()


def journal_preview(path, level, start_ms, end_ms, limit, offset=0):
    if not path.is_file():
        return []
    db = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
    try:
        if level == "tick":
            config = db.execute("SELECT base_level FROM config").fetchone()
            if not config or config[0] != "tick":
                return []
            return [{"ts_event_ns": str(stamp * 1000000), "trade_id": str(identity), "price": price, "size": size,
                     "aggressor_side": "SELL" if maker else "BUY"}
                    for identity, stamp, price, size, maker in db.execute(
                        "SELECT * FROM trades WHERE timestamp_ms>=? AND timestamp_ms<? ORDER BY timestamp_ms,id LIMIT ? OFFSET ?",
                        (start_ms, end_ms, limit, offset))]
        return [{"ts_event_ns": str((value["end_ms"] - 1) * 1000000),
                 **{key: value[key] for key in ("open", "high", "low", "close", "volume")},
                 "is_complete": str(value["is_complete"])}
                for row in db.execute("SELECT value FROM candles WHERE interval=? AND end_ms>? AND end_ms<=? AND coalesce(json_extract(value,'$.retained'),1)=1 ORDER BY start_ms LIMIT ? OFFSET ?",
                                      (level, start_ms, end_ms, limit, offset)) if (value := json.loads(row[0]))]
    finally:
        db.close()


def journal_page(path, level, start_ms, end_ms, limit, cursor=None):
    import base64
    import hashlib

    if end_ms <= start_ms or end_ms - start_ms > 900000:
        raise ValueError("预览须为不超过 15 分钟的正向范围")
    def fingerprint():
        return hashlib.sha256(str([(item.name, item.stat().st_size, item.stat().st_mtime_ns)
                                  for item in (path, path.with_name(path.name + "-wal")) if item.exists()]).encode()).hexdigest()
    snapshot = fingerprint()
    query = [level, start_ms, end_ms, str(path), snapshot]
    offset = 0
    if cursor:
        try:
            value = json.loads(base64.urlsafe_b64decode(cursor.removeprefix("journal.")))
            if value["query"] != query or type(value["offset"]) is not int or not 0 <= value["offset"] <= 2000000:
                raise ValueError
            offset = value["offset"]
        except (ValueError, KeyError, TypeError):
            raise ValueError("游标无效或数据已变化，请重新查询") from None
    records = journal_preview(path, level, start_ms, end_ms, limit + 1, offset)
    if fingerprint() != snapshot:
        raise ValueError("查询期间数据已变化，请重试")
    following = "journal." + base64.urlsafe_b64encode(json.dumps({"query": query, "offset": offset + limit}).encode()).decode() if len(records) > limit else None
    return {"records": records[:limit], "snapshot": snapshot, "next_cursor": following, "quality": "unverified"}
