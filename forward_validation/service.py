"""Read-only public Binance collector, restartable under the user's systemd manager."""

import argparse
import fcntl
import html
import importlib.metadata
import json
import math
import os
import shutil
import signal
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

from .core import (
    DAY,
    DEFAULT_ROOT,
    HOUR,
    MINUTE,
    PARAMETERS,
    PROJECT,
    SYMBOLS,
    SignalState,
    archive_response,
    connect,
    incident,
    make_quote,
    meta,
    observe_trades,
    save_quote,
    set_meta,
    settle,
    sha,
    source_files,
    utc,
    verify,
    write_json,
)

ALLOWED = {"time", "exchangeInfo", "klines", "aggTrades", "fundingRate"}


class PublicAPI:
    def __init__(self, root, db):
        self.root, self.db = Path(root), db
        self.clock_base, self.clock_mono = 0, 0
        self.clock_uncertainty_ms = 0
        self.healthy = False

    def now(self):
        return (
            int(self.clock_base + (time.monotonic() - self.clock_mono) * 1000)
            if self.clock_base
            else int(time.time() * 1000)
        )

    def get(self, endpoint, **parameters):
        if endpoint not in ALLOWED:
            raise ValueError("Only the frozen public GET endpoints are permitted")
        if "symbol" in parameters and parameters["symbol"] not in SYMBOLS:
            raise ValueError("Unexpected symbol")
        url = "https://fapi.binance.com/fapi/v1/" + endpoint
        if parameters:
            url += "?" + urllib.parse.urlencode(parameters)
        request = urllib.request.Request(
            url, headers={"User-Agent": "Nautilus-forward-observation/1.0"}, method="GET"
        )
        with urllib.request.urlopen(request, timeout=12) as response:
            if urllib.parse.urlsplit(response.url).hostname != "fapi.binance.com":
                raise RuntimeError("Unexpected public API redirect")
            body = response.read(8 * 1024 * 1024 + 1)
            if len(body) > 8 * 1024 * 1024:
                raise RuntimeError("Public response exceeded size bound")
        payload = json.loads(body)
        if isinstance(payload, dict) and "code" in payload and int(payload["code"]) < 0:
            raise RuntimeError("Public API error: " + str(payload))
        archive_response(self.root, self.db, endpoint, parameters, payload, self.now())
        return payload

    def confirmed_now(self):
        """Lower clock bound for deciding that a candle has definitely closed."""
        return self.now() - self.clock_uncertainty_ms

    def sync_clock(self):
        old = self.now() if self.clock_base else None
        began = time.monotonic()
        data = self.get("time")
        elapsed = time.monotonic() - began
        # Upper bound prevents backdating a quote when the host clock is behind.
        self.clock_base = int(data["serverTime"]) + int(elapsed * 1000)
        self.clock_uncertainty_ms = int(elapsed * 1000)
        self.clock_mono = time.monotonic()
        self.healthy = elapsed <= 2 and (
            old is None or abs(self.clock_base - old - elapsed * 1000) <= 5000
        )
        with self.db:
            set_meta(
                self.db,
                "clock",
                {
                    "server_ms": self.clock_base,
                    "host_ms": int(time.time() * 1000),
                    "roundtrip_ms": round(elapsed * 1000),
                    "healthy": self.healthy,
                },
            )


def parse_rules(exchange):
    result = {}
    for symbol in exchange["symbols"]:
        if symbol["symbol"] not in SYMBOLS:
            continue
        filters = {x["filterType"]: x for x in symbol["filters"]}
        result[symbol["symbol"]] = {
            "tick": filters["PRICE_FILTER"]["tickSize"],
            "step": filters["LOT_SIZE"]["stepSize"],
            "min_qty": filters["LOT_SIZE"]["minQty"],
            "max_qty": filters["LOT_SIZE"]["maxQty"],
            "min_notional": filters["MIN_NOTIONAL"]["notional"],
            "status": symbol["status"],
            "contract_type": symbol["contractType"],
        }
    if set(result) != set(SYMBOLS) or any(
        r["status"] != "TRADING" or r["contract_type"] != "PERPETUAL" for r in result.values()
    ):
        raise RuntimeError("Expected two active USD-M perpetual contracts")
    return result


def store_bars(db, symbol, rows, received_ms, closed_ms=None):
    new = []
    for k in sorted(rows, key=lambda x: int(x[0])):
        start, end = int(k[0]), int(k[6]) + 1
        if end > (received_ms if closed_ms is None else closed_ms) - 1000:
            continue
        prices = tuple(float(k[i]) for i in (1, 2, 3, 4))
        o, h, lo, c = prices
        if (
            start % MINUTE
            or end != start + MINUTE
            or any(not math.isfinite(p) or p <= 0 for p in prices)
            or lo > min(o, c)
            or h < max(o, c)
            or lo > h
        ):
            raise RuntimeError("Malformed or unclosed minute bar")
        old = db.execute(
            "SELECT open,high,low,close FROM bars WHERE symbol=? AND end_ms=?", (symbol, end)
        ).fetchone()
        if old and tuple(old) != prices:
            raise RuntimeError(f"Previously recorded bar was revised: {symbol} {end}")
        if not old:
            db.execute(
                "INSERT INTO bars VALUES(?,?,?,?,?,?,?)", (symbol, end, *prices, received_ms)
            )
            new.append((end, *prices))
    return new


def initialize(root):
    root = Path(root)
    if (root / "manifest.json").exists():
        verify(root)
        raise RuntimeError("Study already frozen; use serve or status")
    db = connect(root)
    api = PublicAPI(root, db)
    api.sync_clock()
    if not api.healthy:
        raise RuntimeError("Clock check failed")
    rules = parse_rules(api.get("exchangeInfo"))
    now = api.now()
    end = api.confirmed_now() // MINUTE * MINUTE
    for symbol in SYMBOLS:
        cursor = end - 3 * DAY
        while cursor < end:
            rows = api.get(
                "klines",
                symbol=symbol,
                interval="1m",
                startTime=cursor,
                endTime=end - 1,
                limit=1000,
            )
            if not rows:
                raise RuntimeError("Warmup missing minute bars")
            with db:
                store_bars(db, symbol, rows, api.now(), api.confirmed_now())
            cursor = int(rows[-1][0]) + MINUTE
        state = SignalState()
        for row in db.execute("SELECT * FROM bars WHERE symbol=? ORDER BY end_ms", (symbol,)):
            state.update(row["end_ms"], row["open"], row["high"], row["low"], row["close"])
        if len(state.adverse) < 54 or len(state.closes) < 1441:
            raise RuntimeError("Warmup cannot initialize market features")
        with db:
            set_meta(
                db,
                symbol + ":warmup_bars",
                db.execute("SELECT COUNT(*) FROM bars WHERE symbol=?", (symbol,)).fetchone()[0],
            )
    api.sync_clock()
    now = api.now()
    start = (now // (5 * MINUTE) + 1) * 5 * MINUTE
    manifest = {
        "created_at": utc(now),
        "start_ms": start,
        "start_utc": utc(start),
        "end_ms": start + PARAMETERS["study_days"] * DAY,
        "end_utc": utc(start + PARAMETERS["study_days"] * DAY),
        "review_ms": start + PARAMETERS["study_days"] * DAY + 25 * HOUR,
        "parameters": PARAMETERS,
        "rules": rules,
        "source_sha256": {str(p.relative_to(PROJECT)): sha(p) for p in source_files()},
        "environment": {
            "python": sys.version,
            "packages": {
                name: importlib.metadata.version(name)
                for name in ("numpy", "pandas", "nautilus_trader")
            },
        },
        "mode": "public_data_observation_no_orders",
    }
    write_json(root / "manifest.json", manifest)
    with zipfile.ZipFile(root / "frozen_sources.zip", "w", zipfile.ZIP_DEFLATED) as z:
        for p in source_files():
            z.write(p, str(p.relative_to(PROJECT)))
        z.write(root / "manifest.json", "manifest.json")
    with db:
        set_meta(db, "rules_checked_ms", now)
        set_meta(db, "rules_ok", True)
        set_meta(db, "state", "initialized")
    write_json(
        root / "archive.json",
        {
            "sha256": sha(root / "frozen_sources.zip"),
            "manifest_sha256": sha(root / "manifest.json"),
        },
    )
    publish(root, db, manifest, now, "等待前向起点", None)
    db.close()
    print(
        json.dumps(
            {"start": manifest["start_utc"], "end": manifest["end_utc"], "root": str(root)},
            ensure_ascii=False,
        )
    )


class Collector:
    def __init__(self, root):
        self.root = Path(root)
        self.manifest = verify(root)
        self.db = connect(root)
        self.api = PublicAPI(root, self.db)
        self.states = {}
        for symbol in SYMBOLS:
            state = SignalState()
            for r in self.db.execute(
                "SELECT * FROM bars WHERE symbol=? ORDER BY end_ms", (symbol,)
            ):
                state.update(r["end_ms"], r["open"], r["high"], r["low"], r["close"])
            self.states[symbol] = state
        self.last_minute = -1
        self.last_rules = meta(self.db, "rules_checked_ms", 0)
        self.last_funding = 0
        self.last_disk = 0

    def trades(self, symbol):
        now = self.api.now()
        last = meta(self.db, symbol + ":trade_id")
        last_ts = meta(self.db, symbol + ":trade_ts")
        if last_ts is not None and now - last_ts > 47 * HOUR:
            with self.db:
                incident(
                    self.db,
                    now,
                    symbol,
                    "unrecoverable_trade_gap",
                    {"start_ms": last_ts, "end_ms": now - MINUTE},
                )
                self.db.execute(
                    "UPDATE quotes SET gapped=1 WHERE symbol=? AND expires_ms>? AND active_ms<?",
                    (symbol, last_ts, now - MINUTE),
                )
                set_meta(self.db, symbol + ":trade_id", None)
                set_meta(self.db, symbol + ":trade_ts", None)
            last = None
        for _ in range(4):
            params = {"symbol": symbol, "limit": 1000}
            if last is None:
                params.update(startTime=now - MINUTE, endTime=now)
            else:
                params["fromId"] = last + 1
            data = self.api.get("aggTrades", **params)
            if not isinstance(data, list):
                raise TypeError("Aggregate trades response is not an array")
            for t in data:
                if (
                    int(t["T"]) > self.api.now() + 2000
                    or not isinstance(t["m"], bool)
                    or not math.isfinite(float(t["p"]))
                    or not math.isfinite(float(t["q"]))
                    or float(t["p"]) <= 0
                    or float(t["q"]) < 0
                ):
                    raise RuntimeError("Malformed aggregate trade")
            with self.db:
                observe_trades(self.db, symbol, data)
            previous = last
            last = meta(self.db, symbol + ":trade_id")
            if len(data) < 1000 or last == previous:
                break

    def bars(self, symbol):
        last = self.db.execute("SELECT MAX(end_ms) FROM bars WHERE symbol=?", (symbol,)).fetchone()[
            0
        ]
        target = (self.api.confirmed_now() - 1200) // MINUTE * MINUTE
        for _ in range(5):
            if last >= target:
                break
            rows = self.api.get(
                "klines",
                symbol=symbol,
                interval="1m",
                startTime=last,
                endTime=target - 1,
                limit=1000,
            )
            if not rows:
                break
            # Commit candles and their quotes together. A crash cannot recreate an old quote as live.
            with self.db:
                new = store_bars(self.db, symbol, rows, self.api.now(), self.api.confirmed_now())
                for end, o, h, lo, c in new:
                    if end <= last:
                        raise RuntimeError("Late bar insertion requires a new feature replay")
                    if end != last + MINUTE:
                        incident(
                            self.db,
                            self.api.now(),
                            symbol,
                            "minute_gap",
                            {"previous_ms": last, "next_ms": end},
                        )
                    snapshot = self.states[symbol].update(end, o, h, lo, c)
                    if snapshot is not None and end >= self.manifest["start_ms"]:
                        trade_ts = meta(self.db, symbol + ":trade_ts", 0) or 0
                        healthy = (
                            self.api.healthy
                            and self.api.now() - trade_ts <= 15_000
                            and meta(self.db, "rules_ok", False)
                            and meta(self.db, symbol + ":funding_covered_ms", 0)
                            >= self.api.now() - HOUR
                        )
                        quote = make_quote(
                            symbol,
                            end,
                            snapshot,
                            self.api.now(),
                            self.manifest["rules"][symbol],
                            self.manifest,
                            healthy,
                        )
                        save_quote(self.db, quote)
                    last = end
            if not new or len(rows) < 1000:
                break

    def funding(self, symbol):
        target = self.api.now()
        cursor = meta(self.db, symbol + ":funding_covered_ms", self.manifest["start_ms"] - DAY)
        cursor = max(self.manifest["start_ms"] - DAY, cursor - DAY)
        while cursor < target:
            data = self.api.get(
                "fundingRate", symbol=symbol, startTime=cursor, endTime=target, limit=1000
            )
            with self.db:
                for r in data:
                    ts, rate, mark = (
                        int(r["fundingTime"]),
                        float(r["fundingRate"]),
                        float(r["markPrice"]),
                    )
                    if (
                        not math.isfinite(rate)
                        or not math.isfinite(mark)
                        or mark <= 0
                        or ts > target
                    ):
                        raise RuntimeError("Incomplete funding record")
                    key = (symbol, ts, r.get("rateType", "Regular"))
                    old = self.db.execute(
                        "SELECT rate,mark FROM funding WHERE symbol=? AND ts=? AND rate_type=?", key
                    ).fetchone()
                    if old and (float(old[0]), float(old[1])) != (rate, mark):
                        raise RuntimeError("Historical funding record changed")
                    self.db.execute(
                        "INSERT OR IGNORE INTO funding VALUES(?,?,?,?,?)",
                        (*key, r["fundingRate"], r["markPrice"]),
                    )
            if len(data) < 1000:
                break
            cursor = int(data[-1]["fundingTime"]) + 1
        with self.db:
            # Settlement publication can lag the funding timestamp.
            set_meta(self.db, symbol + ":funding_covered_ms", target - 15 * MINUTE)

    def cycle(self):
        now = self.api.now()
        if now // MINUTE != self.last_minute:
            self.api.sync_clock()
            self.manifest = verify(self.root)
            self.last_minute = self.api.now() // MINUTE
        now = self.api.now()
        if now - self.last_rules > DAY:
            actual = parse_rules(self.api.get("exchangeInfo"))
            same = actual == self.manifest["rules"]
            with self.db:
                set_meta(self.db, "rules_ok", same)
                set_meta(self.db, "rules_checked_ms", now)
                if not same:
                    incident(self.db, now, "ALL", "instrument_rules_changed", actual)
            self.last_rules = now
        if now - self.last_disk > HOUR:
            used = sum(p.stat().st_size for p in self.root.rglob("*") if p.is_file())
            if used > 20 * 1024**3 or shutil.disk_usage(self.root).free < 2 * 1024**3:
                raise RuntimeError("Storage budget reached; collection paused")
            self.last_disk = now
        if now - self.last_funding > 15 * MINUTE:
            for symbol in SYMBOLS:
                self.funding(symbol)
            self.last_funding = now
        for symbol in SYMBOLS:
            self.trades(symbol)
            self.bars(symbol)
            with self.db:
                settle(self.db, symbol, self.api.now())
        publish(self.root, self.db, self.manifest, self.api.now(), "持续观察中", None)


def publish(root, db, manifest, now, state, error):
    root = Path(root)
    rows = []
    observed_until = min(now, manifest["end_ms"] - 1)
    expected = (
        max(0, (observed_until - manifest["start_ms"]) // (5 * MINUTE) + 1)
        if now >= manifest["start_ms"]
        else 0
    )
    for symbol in SYMBOLS:
        row = {
            "symbol": symbol,
            "last_bar_ms": db.execute(
                "SELECT MAX(end_ms) FROM bars WHERE symbol=?", (symbol,)
            ).fetchone()[0],
            "last_trade_ms": meta(db, symbol + ":trade_ts"),
            "last_trade_id": meta(db, symbol + ":trade_id"),
            "bars": db.execute("SELECT COUNT(*) FROM bars WHERE symbol=?", (symbol,)).fetchone()[0],
            "expected_decisions": expected,
        }
        row["quote_status"] = {
            r[0]: r[1]
            for r in db.execute(
                "SELECT status,COUNT(*) FROM quotes WHERE symbol=? AND decision_ms>=? AND decision_ms<? GROUP BY status",
                (symbol, manifest["start_ms"], manifest["end_ms"]),
            )
        }
        for field, sql in (
            ("prospective_quotes", "SELECT COUNT(*) FROM quotes WHERE symbol=? AND prospective=1"),
            (
                "eligible_quotes",
                "SELECT COUNT(*) FROM quotes WHERE symbol=? AND prospective=1 AND filter_ok=1",
            ),
            ("strict_touches", "SELECT COUNT(*) FROM touches WHERE symbol=? AND kind='strict'"),
            ("broad_touches", "SELECT COUNT(*) FROM touches WHERE symbol=? AND kind='broad'"),
            (
                "mature_strict_events",
                "SELECT COUNT(*) FROM outcomes WHERE symbol=? AND kind='strict' AND horizon=24 AND status='complete'",
            ),
        ):
            row[field] = db.execute(sql, (symbol,)).fetchone()[0]
        row["feed_lag_seconds"] = (
            (now - row["last_trade_ms"]) / 1000 if row["last_trade_ms"] else None
        )
        rows.append(row)
    status = {
        "updated_at": utc(now),
        "updated_ms": now,
        "state": state,
        "error": error,
        "pid": os.getpid(),
        "mode": "行情观察与模拟限价",
        "start_utc": manifest["start_utc"],
        "end_utc": manifest["end_utc"],
        "review_utc": utc(manifest["review_ms"]),
        "parameters_frozen": True,
        "rules_unchanged": meta(db, "rules_ok", False),
        "clock": meta(db, "clock"),
        "markets": rows,
        "incidents": db.execute("SELECT COUNT(*) FROM incidents").fetchone()[0],
        "assessment": "等待观察期及结果成熟；期间不做盈利判定"
        if now < manifest["review_ms"]
        else "观察期结束，查看最终评估",
    }
    write_json(root / "status.json", status)
    with db:
        set_meta(db, "last_status", status)
    heading = html.escape(state)
    body = f"<h1>BTC / ETH 前向观察</h1><p id='health'>{heading}</p><p>更新时间：{html.escape(status['updated_at'])}</p><p>观察期：{manifest['start_utc']} 至 {manifest['end_utc']}<br>评估时间：{status['review_utc']}</p>"
    if error:
        body += "<p class='bad'>" + html.escape(error) + "</p>"
    body += "<p>固定规则的行情观察与模拟限价。触价是研究代理，不能视为真实订单成交。当前仅展示采集状态和样本数量。</p><table><tr><th>市场</th><th>分钟线</th><th>已记录报价</th><th>通过过滤报价</th><th>普通触价</th><th>保守触价</th><th>24h已成熟</th><th>交易流延迟秒</th></tr>"
    for row in rows:
        body += (
            "<tr>"
            + "".join(
                f"<td>{html.escape(str(row[k]))}</td>"
                for k in (
                    "symbol",
                    "bars",
                    "prospective_quotes",
                    "eligible_quotes",
                    "broad_touches",
                    "strict_touches",
                    "mature_strict_events",
                    "feed_lag_seconds",
                )
            )
            + "</tr>"
        )
    body += (
        "</table><h2>记录状态</h2><pre>"
        + html.escape(json.dumps(rows, indent=2, ensure_ascii=False))
        + "</pre><p>故障、断线和迟到均保留记录。源文件或市场规则变化会暂停新候选，不能继续沿用过时状态。</p><p><a href='manifest.json'>冻结清单</a> · <a href='status.json'>状态明细</a> · <a href='review.html'>到期评估</a></p>"
    )
    page = (
        "<!doctype html><html lang='zh-CN'><meta charset='utf-8'><meta http-equiv='refresh' content='60'><title>BTC/ETH前向观察</title><style>body{max-width:1100px;margin:32px auto;font:16px/1.6 system-ui;padding:20px;color:#223}table{border-collapse:collapse}td,th{padding:9px;border:1px solid #ccd}pre{overflow:auto;background:#f5f6f8;padding:16px}.bad{color:#a21;font-weight:bold}</style><body>"
        + body
    )
    page += f"<script>if(Date.now()-{int(now)}>300000){{document.getElementById('health').textContent='状态已超过五分钟未更新：请检查服务或网络';document.getElementById('health').className='bad';}}</script></body></html>"
    temp = root / "status.html.tmp"
    temp.write_text(page)
    temp.replace(root / "status.html")
    if not (root / "review.html").exists():
        (root / "review.html").write_text(
            "<!doctype html><meta charset='utf-8'><h1>前向验证尚未到期</h1><p>"
            + status["review_utc"]
            + " 后按冻结规则评估。<a href='status.html'>返回采集状态</a></p>"
        )


def serve(root):
    root = Path(root)
    lock = (root / "collector.lock").open("a+")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    collector = Collector(root)
    stopping = False

    def stop(signum, frame):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    failures = 0
    while not stopping:
        began = time.monotonic()
        try:
            collector.cycle()
            failures = 0
            if min(collector.api.now(), int(time.time() * 1000)) >= collector.manifest["review_ms"]:
                from .review import evaluate

                evaluate(root)
                publish(
                    root, collector.db, collector.manifest, collector.api.now(), "观察期结束", None
                )
                break
        except Exception as exc:  # noqa: BLE001 -- preserve fault evidence and rebuild rolled-back feature state
            failures += 1
            error = f"{type(exc).__name__}: {exc}"
            now = collector.api.now()
            with collector.db:
                incident(collector.db, now, "ALL", "collector_error", error)
            publish(root, collector.db, collector.manifest, now, "采集异常，等待重试", error)
            print(error, file=sys.stderr, flush=True)
            # Rebuild feature state after any transaction rollback.
            collector.db.close()
            collector = Collector(root)
        delay = (
            min(300, 10 * 2 ** min(failures, 5))
            if failures
            else max(0.5, 5 - (time.monotonic() - began))
        )
        until = time.monotonic() + delay
        while not stopping and time.monotonic() < until:
            time.sleep(min(1, until - time.monotonic()))
    if stopping:
        publish(root, collector.db, collector.manifest, collector.api.now(), "采集已停止", None)
    collector.db.close()
    lock.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("init", "serve", "status", "verify", "review"))
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    args = parser.parse_args()
    if args.action == "init":
        initialize(args.root)
    elif args.action == "serve":
        serve(args.root)
    elif args.action == "verify":
        verify(args.root)
        print("Frozen sources verified")
    elif args.action == "review":
        from .review import evaluate

        evaluate(args.root)
    else:
        print((args.root / "status.json").read_text())


if __name__ == "__main__":
    main()
