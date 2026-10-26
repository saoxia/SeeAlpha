"""Read-only observation health; never changes the frozen study or its database."""

import argparse
import hashlib
import html
import json
import os
import sqlite3
import tempfile
import time
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

PROJECT = Path(__file__).resolve().parents[1]
DEFAULT_ROOT = PROJECT / "data/forward_validation/btc_eth_mrl_20260921"
MINUTE = 60_000
HOUR = 60 * MINUTE


def digest(path):
    with Path(path).open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def atomic_write(path, text):
    path = Path(path)
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as temporary:
        temporary.write(text)
        temporary.flush()
        os.fsync(temporary.fileno())
        name = temporary.name
    os.replace(name, path)


def verify_freeze(root, manifest, project=PROJECT):
    """Check archived and working bytes without importing trading dependencies."""
    problems = []
    try:
        archive = read_json(root / "archive.json")
        for file, field in (("manifest.json", "manifest_sha256"), ("frozen_sources.zip", "sha256")):
            if digest(root / file) != archive[field]:
                problems.append(file)
        for path, expected in manifest["source_sha256"].items():
            if digest(project / path) != expected:
                problems.append(path)
    except (OSError, ValueError, KeyError) as exc:
        problems.append(str(exc))
    return problems


def snapshot(root=DEFAULT_ROOT, *, now_ms=None, project=PROJECT):
    root = Path(root)
    manifest = read_json(root / "manifest.json")
    status = read_json(root / "status.json")
    host_now = int(time.time() * 1000) if now_ms is None else int(now_ms)
    clock = status.get("clock") or {}
    # The collector records exchange time; account for the measured host offset.
    offset = clock.get("server_ms", 0) - clock.get("host_ms", 0)
    now = host_now + offset
    age = max(0, now - status["updated_ms"])
    problems, notices, markets = [], [], []
    frozen_changes = verify_freeze(root, manifest, project)
    if frozen_changes:
        problems.append(
            {"code": "frozen_source_changed", "text": "冻结文件校验失败，需处理版本变化"}
        )
    completion = root / "review_completion.json"
    reviewed = completion.exists() and read_json(completion).get("state") == "completed"
    ended = (status["state"] == "观察期结束" or reviewed) and now >= manifest["review_ms"]
    if status["state"] == "采集已停止" and not ended:
        problems.append({"code": "stopped", "text": "采集服务已停止"})
    if not ended and age > 5 * MINUTE:
        problems.append({"code": "stale_heartbeat", "text": "采集状态超过五分钟未更新"})
    if status.get("error") and not ended:
        problems.append({"code": "collector_error", "text": "采集服务报告异常"})
    if not ended and not clock.get("healthy", False):
        problems.append({"code": "clock_unhealthy", "text": "行情时钟尚未通过校验"})
    if not status.get("rules_unchanged", False) and not ended:
        problems.append({"code": "rules_changed", "text": "合约规则变化，候选已暂停"})
    # Exclude a still-open decision deadline from today's coverage denominator.
    until = min(now - manifest["parameters"]["decision_deadline_ms"], manifest["end_ms"] - 1)
    expected = max(0, (until - manifest["start_ms"]) // (5 * MINUTE) + 1)
    total = (manifest["end_ms"] - manifest["start_ms"]) // (5 * MINUTE)
    with closing(sqlite3.connect(f"file:{root / 'observations.sqlite'}?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        db.execute("BEGIN")  # All health counts describe the same database snapshot.
        metadata = {r[0]: json.loads(r[1]) for r in db.execute("SELECT key,value FROM meta")}
        for symbol in manifest["rules"]:
            trade_ts = metadata.get(symbol + ":trade_ts")
            bar_ts = db.execute(
                "SELECT MAX(end_ms) FROM bars WHERE symbol=?", (symbol,)
            ).fetchone()[0]
            funding_ts = metadata.get(symbol + ":funding_covered_ms", 0)
            lag = max(0, now - trade_ts) if trade_ts is not None else None
            bar_lag = max(0, now - bar_ts) if bar_ts is not None else None
            if not ended and (lag is None or lag > 15_000):
                problems.append(
                    {
                        "code": f"trade_lag:{symbol}",
                        "text": f"{symbol} 成交流尚未追上，暂不能形成实时候选",
                    }
                )
            if not ended and (bar_lag is None or bar_lag > 2 * MINUTE):
                problems.append({"code": f"bar_lag:{symbol}", "text": f"{symbol} 分钟行情有积压"})
            if not ended and now - funding_ts > HOUR:
                problems.append(
                    {"code": f"funding_lag:{symbol}", "text": f"{symbol} 资金费率覆盖尚未追上"}
                )
            observed = db.execute(
                "SELECT COUNT(*) FROM quotes WHERE symbol=? AND prospective=1 AND decision_ms>=? AND decision_ms<=?",
                (symbol, manifest["start_ms"], until),
            ).fetchone()[0]
            missing = max(0, expected - observed)
            touches = db.execute(
                """SELECT COUNT(*) FROM touches t JOIN quotes q USING(symbol,decision_ms)
                WHERE t.symbol=? AND t.kind='strict' AND q.prospective=1 AND q.armed=1 AND q.gapped=0 AND q.filter_ok=1
                AND q.decision_ms>=? AND q.decision_ms<?""",
                (symbol, manifest["start_ms"], manifest["end_ms"]),
            ).fetchone()[0]
            coverage = observed / expected if expected else None
            ceiling = max(0, (total - missing) / total)
            if coverage is not None and coverage < manifest["parameters"]["minimum_coverage"]:
                notices.append(
                    {
                        "code": f"coverage:{symbol}",
                        "text": f"{symbol} 目前报价覆盖率偏低，缺失机会保留记录，不回填实时信号",
                    }
                )
            markets.append(
                {
                    "symbol": symbol,
                    "trade_lag_seconds": lag / 1000 if lag is not None else None,
                    "bar_lag_seconds": bar_lag / 1000 if bar_lag is not None else None,
                    "expected_decisions": expected,
                    "prospective_quotes": observed,
                    "missed_decisions": missing,
                    "quote_coverage": coverage,
                    "maximum_final_coverage": ceiling,
                    "eligible_strict_touches": touches,
                    "last_trade_id": metadata.get(symbol + ":trade_id"),
                }
            )
        incidents = db.execute("SELECT COUNT(*) FROM incidents").fetchone()[0]
        recent_errors = db.execute(
            "SELECT COUNT(*) FROM incidents WHERE ts>=? AND kind='collector_error'", (now - HOUR,)
        ).fetchone()[0]
    codes = {p["code"] for p in problems}
    if ended and not problems:
        state = "观察期结束"
    elif codes & {
        "frozen_source_changed",
        "stopped",
        "stale_heartbeat",
        "collector_error",
        "rules_changed",
    }:
        state = "需要处理"
    elif problems:
        state = "正在追赶或等待行情"
    else:
        state = "实时采集正常"
    return {
        "checked_at": datetime.fromtimestamp(host_now / 1000, UTC).isoformat(),
        "checked_ms": host_now,
        "state": state,
        "healthy": not problems,
        "problems": problems,
        "notices": notices,
        "heartbeat_age_seconds": age / 1000,
        "clock_roundtrip_ms": clock.get("roundtrip_ms"),
        "markets": markets,
        "incidents_total": incidents,
        "collector_errors_last_hour": recent_errors,
        "collector_pid": status.get("pid"),
        "frozen_changes": frozen_changes,
        "start_ms": manifest["start_ms"],
        "end_ms": manifest["end_ms"],
        "review_ms": manifest["review_ms"],
        "study_dates_unchanged": True,
        "assessment": "到期统一评估；健康检查不计算盈利，也不修改样本",
    }


def china_time(ms):
    return datetime.fromtimestamp(ms / 1000, ZoneInfo("Asia/Shanghai")).strftime(
        "%Y-%m-%d %H:%M:%S"
    )


def duration(seconds):
    if seconds is None:
        return "尚无数据"
    if seconds >= 3600:
        return f"{seconds / 3600:.2f} 小时"
    if seconds >= 60:
        return f"{seconds / 60:.1f} 分钟"
    return f"{seconds:.1f} 秒"


def render(result):
    esc = html.escape
    rows = []
    for r in result.get("markets", []):
        coverage = f"{r['quote_coverage']:.1%}" if r["quote_coverage"] is not None else "尚未开始"
        values = (
            r["symbol"],
            duration(r["trade_lag_seconds"]),
            coverage,
            r["missed_decisions"],
            f"{r['maximum_final_coverage']:.1%}",
            r["eligible_strict_touches"],
        )
        rows.append("<tr>" + "".join(f"<td>{esc(str(x))}</td>" for x in values) + "</tr>")
    problems = "".join(
        f"<li>{esc(p['text'])}</li>" for p in result["problems"] + result.get("notices", [])
    )
    dates = ""
    if "start_ms" in result:
        dates = f"<p>北京时间：{china_time(result['start_ms'])} 开始；{china_time(result['end_ms'])} 结束收集；{china_time(result['review_ms'])} 起评估。</p>"
    return f"""<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="refresh" content="60">
<title>BTC / ETH 采集健康</title><style>body{{max-width:1000px;margin:40px auto;padding:20px;font:16px/1.7 system-ui;color:#243247}}table{{border-collapse:collapse;width:100%}}th,td{{padding:10px;border-bottom:1px solid #ccd;text-align:left}}.warn{{color:#9c4b00}}.ok{{color:#176746}}small{{color:#667}}@media(max-width:700px){{.table{{overflow:auto}}}}</style>
<h1>BTC / ETH 采集健康</h1><h2 id="health" class="{"ok" if result["healthy"] else "warn"}">{esc(result["state"])}</h2>
<p>检查时间：{china_time(result["checked_ms"])}（北京时间）</p>{dates}<ul>{problems}</ul>
<div class="table"><table><tr><th>市场</th><th>行情延迟</th><th>当前报价覆盖</th><th>已错过机会</th><th>最终覆盖率上限</th><th>有效保守触价</th></tr>{"".join(rows)}</table></div>
<p>关机或断网后会补回公开行情，过去错过的实时报价不会补造。最终覆盖率上限假设此后没有继续缺失；它不是收益预测。观察期和门槛保持原样。</p>
<p>这是行情与模拟限价观察，不下真实订单。到期前只检查采集和样本质量，不按盈利调整参数。</p>
<p>累计故障记录：{result.get("incidents_total", "未知")}；最近一小时采集错误：{result.get("collector_errors_last_hour", "未知")}。</p>
<p><a href="quality.html">每日样本质量</a> · <a href="status.html">采集计数明细</a> · <a href="health.json">健康记录</a> · <a href="operations.json">运维与异常</a> · <a href="review.html">到期评估</a></p>
<script>if({str(result["state"] != "观察期结束").lower()} && Date.now()-{result["checked_ms"]}>180000){{document.getElementById('health').textContent='健康检查超过三分钟未更新，请检查电脑及后台服务';document.getElementById('health').className='warn';}}</script></html>"""


def inspect(root=DEFAULT_ROOT, *, write=False):
    root = Path(root)
    try:
        result = snapshot(root)
    except (OSError, ValueError, KeyError, sqlite3.Error) as exc:
        now = int(time.time() * 1000)
        result = {
            "checked_ms": now,
            "checked_at": datetime.fromtimestamp(now / 1000, UTC).isoformat(),
            "state": "健康检查无法读取数据",
            "healthy": False,
            "markets": [],
            "problems": [{"code": "unreadable", "text": f"{type(exc).__name__}: {exc}"}],
        }
    if write:
        # Do not manufacture a study directory after a typo or missing dataset.
        if not root.is_dir():
            raise FileNotFoundError(root)
        try:
            previous = read_json(root / "health.json") if (root / "health.json").exists() else {}
        except (OSError, ValueError):
            previous = {}
        fingerprint = [result["state"], sorted(p["code"] for p in result["problems"])]
        old = [previous.get("state"), sorted(p["code"] for p in previous.get("problems", []))]
        if fingerprint != old:
            with (root / "health_changes.jsonl").open("a") as history:
                history.write(json.dumps(result, ensure_ascii=False, allow_nan=False) + "\n")
        atomic_write(
            root / "health.json",
            json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        )
        atomic_write(root / "health.html", render(result))
    return result


def main():
    parser = argparse.ArgumentParser(description="只读核查前向观察健康；不访问网络、账户或修改研究")
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--write", action="store_true", help="更新独立健康页和状态变化记录")
    parser.add_argument("--check", action="store_true", help="采集不健康时返回非零状态")
    args = parser.parse_args()
    result = inspect(args.root, write=args.write)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return int(args.check and not result["healthy"])


if __name__ == "__main__":
    raise SystemExit(main())
