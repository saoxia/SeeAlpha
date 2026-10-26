"""Outcome-blind sample accounting using the frozen spacing and matching rules."""

import argparse
import html
import json
import math
import sqlite3
import time
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from forward_validation.core import DAY, HOUR, MINUTE, verify
from forward_validation.review import calendar, matched, spaced

from .project_health import DEFAULT_ROOT, atomic_write


def decision_counts(db, symbol, manifest, left, right):
    """Inclusive decision range; exclude the still-open 15-second deadline."""
    step = 5 * MINUTE
    first = max(manifest["start_ms"], ((left + step - 1) // step) * step)
    last = min(right, manifest["end_ms"] - 1)
    expected = max(0, (last - first) // step + 1)
    observed = db.execute(
        "SELECT COUNT(*) FROM quotes WHERE symbol=? AND prospective=1 "
        "AND decision_ms>=? AND decision_ms<=?",
        (symbol, first, last),
    ).fetchone()[0]
    return {
        "expected": expected,
        "observed": observed,
        "missed": max(0, expected - observed),
        "coverage": observed / expected if expected else None,
    }


def quality_frames(db, symbol, manifest, now):
    # Deliberately do not read gross/net returns, prices at exit, or PnL columns.
    events = pd.read_sql_query(
        """SELECT q.decision_ms,q.filter_ok,q.vol,t.ts FROM touches t
        JOIN quotes q USING(symbol,decision_ms) WHERE q.symbol=? AND t.kind='strict'
        AND q.prospective=1 AND q.armed=1 AND q.gapped=0
        AND q.decision_ms>=? AND q.decision_ms<? AND t.ts<=? ORDER BY t.ts""",
        db,
        params=(symbol, manifest["start_ms"], manifest["end_ms"], now),
    )
    complete = pd.read_sql_query(
        """SELECT decision_ms FROM outcomes WHERE symbol=? AND kind='strict'
        AND horizon IN (1,4,24) AND status='complete' AND exit_ms<=?
        GROUP BY decision_ms HAVING COUNT(DISTINCT horizon)=3""",
        db,
        params=(symbol, now),
    )
    events["complete"] = events.decision_ms.isin(complete.decision_ms)
    events = calendar(events)
    bars = pd.read_sql_query(
        "SELECT end_ms,close FROM bars WHERE symbol=? AND end_ms<=? ORDER BY end_ms",
        db,
        params=(symbol, now),
    )
    bars["ts"] = bars.end_ms
    bars["vol"] = np.log(bars.close).diff().rolling(1440).std(ddof=0)
    bars.loc[bars.end_ms.diff(1440) != DAY, "vol"] = np.nan
    valid = pd.Series(True, index=bars.index)
    for hours in (1, 4, 24):
        valid &= bars.end_ms.shift(-hours * 60) - bars.end_ms == hours * HOUR
    bars = calendar(bars)
    controls = bars[
        valid
        & bars.vol.gt(0)
        & bars.ts.ge(manifest["start_ms"])
        & bars.ts.lt(manifest["end_ms"])
        & (bars.ts % (5 * MINUTE) == 0)
    ]
    return events, controls


def summarize_samples(events, controls, seed):
    pool = events[events.complete & events.vol.gt(0)]
    eligible = events[events.filter_ok.astype(bool)]
    rows = []
    for spacing in (1, 24):
        selected = spaced(eligible, spacing)  # Before checking future completeness.
        complete = selected[selected.complete & selected.vol.gt(0)]
        ordinary = matched(complete, controls, seed + spacing)
        touch = matched(complete, pool, seed + spacing + 100)
        common = sorted(set(ordinary) & set(touch))
        rows.append(
            {
                "spacing_hours": spacing,
                "strict_touches": len(events),
                "eligible_touches": len(eligible),
                "spaced_events": len(selected),
                "complete_events": len(complete),
                "pending_or_incomplete": len(selected) - len(complete),
                "ordinary_matched": len(ordinary),
                "touch_matched": len(touch),
                "common_events": len(common),
                "active_days": int((complete.loc[common, "ts"] // DAY).nunique()),
                "match_fraction": len(common) / len(complete) if len(complete) else None,
            }
        )
    return rows


def snapshot(root=DEFAULT_ROOT, *, now_ms=None):
    root = Path(root)
    manifest = verify(root)
    now = int(time.time() * 1000) if now_ms is None else int(now_ms)
    params = manifest["parameters"]
    until = now - params["decision_deadline_ms"]
    total = (manifest["end_ms"] - manifest["start_ms"]) // (5 * MINUTE)
    allowed_missing = math.floor(total * (1 - params["minimum_coverage"]) + 1e-8)
    markets = []
    with closing(sqlite3.connect(f"file:{root / 'observations.sqlite'}?mode=ro", uri=True)) as db:
        db.execute("BEGIN")
        for symbol in manifest["rules"]:
            full = decision_counts(db, symbol, manifest, manifest["start_ms"], until)
            windows = {
                label: decision_counts(db, symbol, manifest, now - length, until)
                for label, length in (("last_24h", DAY), ("last_7d", 7 * DAY))
            }
            events, controls = quality_frames(db, symbol, manifest, now)
            samples = summarize_samples(events, controls, params["seed"])
            main = samples[0]
            full_week = now - manifest["start_ms"] >= 7 * DAY
            markets.append(
                {
                    "symbol": symbol,
                    "coverage": full,
                    **windows,
                    "remaining_missed_budget": allowed_missing - full["missed"],
                    "maximum_final_coverage": max(0, (total - full["missed"]) / total),
                    "samples": samples,
                    "quality_thresholds_currently_met": (
                        main["common_events"] >= params["minimum_events"]
                        and main["active_days"] >= params["minimum_days"]
                        and (main["match_fraction"] or 0) >= params["minimum_matched"]
                        and (full["coverage"] or 0) >= params["minimum_coverage"]
                    ),
                    "operations_week": {
                        "target": 0.99,
                        "full_week_observed": full_week,
                        "passed": full_week and (windows["last_7d"]["coverage"] or 0) >= 0.99,
                    },
                }
            )
    return {
        "checked_ms": now,
        "checked_at": datetime.fromtimestamp(now / 1000, UTC).isoformat(),
        "review_ms": manifest["review_ms"],
        "thresholds": {
            k: params[k]
            for k in ("minimum_events", "minimum_days", "minimum_coverage", "minimum_matched")
        },
        "markets": markets,
        "provisional": True,
        "assessment": "仅统计样本质量；匹配会随新数据变化，最终以冻结到期评估为准",
        "returns_examined": False,
    }


def render(result):
    rows = []
    for market in result["markets"]:
        main = market["samples"][0]
        percent = lambda value: f"{value:.1%}" if value is not None else "—"
        values = [
            market["symbol"],
            market["coverage"]["observed"],
            main["eligible_touches"],
            main["spaced_events"],
            main["complete_events"],
            main["common_events"],
            main["active_days"],
            percent(main["match_fraction"]),
            percent(market["last_24h"]["coverage"]),
            percent(market["last_7d"]["coverage"]),
            market["remaining_missed_budget"],
        ]
        rows.append("<tr>" + "".join(f"<td>{html.escape(str(v))}</td>" for v in values) + "</tr>")
    return (
        "<!doctype html><html lang='zh-CN'><meta charset='utf-8'><meta name='viewport' "
        "content='width=device-width,initial-scale=1'><title>前向样本质量</title>"
        "<style>body{font:16px/1.7 system-ui;margin:30px;color:#243247}"
        "table{border-collapse:collapse}td,th{padding:8px;border:1px solid #ccd}</style>"
        "<h1>前向样本质量</h1><p>更新时间（UTC）：" + result["checked_at"] + "</p>"
        "<p>主队列先按一小时间隔去重，再核对三个窗口完整性和两类对照匹配。"
        "成熟样本需要完整的 24 小时后续数据；启动首日为零不代表采集故障。</p>"
        "<div style='overflow:auto'><table><tr><th>市场</th><th>实时报价</th>"
        "<th>合格严格触价</th><th>去重</th><th>成熟完整</th><th>两类匹配</th>"
        "<th>匹配覆盖天数</th><th>匹配率</th><th>近24小时报价覆盖</th>"
        "<th>近7天报价覆盖</th><th>还可缺失报价数</th></tr>"
        + "".join(rows)
        + "</table></div><p>到期门槛：每市场至少 300 个匹配样本、60 天、"
        "报价覆盖 95%、匹配率 90%。运维目标为完整七天报价覆盖 99%，未满七天不验收。"
        "负数的剩余缺失额度表示原观察期已无法达到覆盖门槛。</p>"
        "<p>样本质量不等于盈利证据。这里不展示收益，不重选参数或延长观察。</p>"
        "<p><a href='health.html'>采集健康</a> · <a href='quality.json'>完整质量记录</a>"
        " · <a href='review.html'>到期评估</a></p></html>"
    )


def publish(root=DEFAULT_ROOT):
    root = Path(root)
    result = snapshot(root)
    text = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    daily = root / "quality/daily"
    daily.mkdir(parents=True, exist_ok=True)
    atomic_write(daily / (result["checked_at"][:10] + ".json"), text)
    atomic_write(root / "quality.json", text)
    atomic_write(root / "quality.html", render(result))
    return result


def main():
    parser = argparse.ArgumentParser(description="只读统计前向样本，不计算盈利")
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    args = parser.parse_args()
    print(json.dumps(publish(args.root), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
