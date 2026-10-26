"""One scheduled assessment of the frozen prospective cohort; no interim optimization."""

import html
import math
import time
from pathlib import Path

import numpy as np
import pandas as pd

from .core import DAY, HOUR, MINUTE, PARAMETERS, SYMBOLS, connect, utc, verify, write_json


def spaced(frame, hours):
    previous, keep = None, []
    for i, row in frame.sort_values("ts").iterrows():
        ts = int(row.ts)
        if previous is None or ts - previous >= hours * HOUR:
            keep.append(i)
            previous = ts
    return frame.loc[keep]


def matched(events, pool, seed):
    rng = np.random.default_rng(seed)
    groups = {}
    for key, rows in pool.groupby(["month", "hour_group"]):
        order = np.argsort(np.log(rows.vol.to_numpy()))
        groups[key] = (np.log(rows.vol.to_numpy())[order], rows.index.to_numpy()[order])
    result = {}
    for i, row in events.iterrows():
        group = groups.get((row.month, row.hour_group))
        if group is None or not row.vol > 0:
            continue
        vol, ii = group
        left, right = np.searchsorted(vol, [math.log(row.vol) - 0.25, math.log(row.vol) + 0.25])
        candidates = ii[left:right]
        candidates = candidates[
            np.abs(pool.loc[candidates, "ts"].to_numpy() - int(row.ts)) > 24 * HOUR
        ]
        if len(candidates):
            result[i] = rng.choice(candidates, 20, replace=True)
    return result


def bounds(timestamps, values, start, end):
    if len(values) == 0:
        return None, None
    day_index = np.asarray(timestamps, dtype=np.int64) // DAY - start // DAY
    n = int((end - 1) // DAY - start // DAY + 1)
    counts = np.bincount(day_index, minlength=n)
    sums = np.bincount(day_index, weights=np.asarray(values), minlength=n)
    rng = np.random.default_rng(PARAMETERS["seed"])
    starts = rng.integers(0, n, (5000, (n + 6) // 7))
    indices = ((starts[:, :, None] + np.arange(7)) % n).reshape(5000, -1)[:, :n]
    denominator = counts[indices].sum(axis=1)
    numerator = sums[indices].sum(axis=1)
    means = numerator[denominator > 0] / denominator[denominator > 0]
    if not len(means):
        return None, None
    tail = 0.05 / (18 * 2)
    lo, hi = np.quantile(means, [tail, 1 - tail])
    return float(lo), float(hi)


def calendar(frame, column="ts"):
    dt = pd.to_datetime(frame[column], unit="ms", utc=True)
    frame["month"] = dt.dt.year * 12 + dt.dt.month
    frame["hour_group"] = dt.dt.hour // 6
    return frame


def common_frame(db, symbol, manifest):
    quotes = pd.read_sql_query(
        """SELECT q.*, t.ts, t.trade_id FROM touches t JOIN quotes q USING(symbol,decision_ms)
        WHERE q.symbol=? AND t.kind='strict' AND q.prospective=1 AND q.armed=1
        AND q.gapped=0 AND q.decision_ms>=? AND q.decision_ms<? ORDER BY t.ts""",
        db,
        params=(symbol, manifest["start_ms"], manifest["end_ms"]),
    )
    outcomes = pd.read_sql_query(
        "SELECT * FROM outcomes WHERE symbol=? AND kind='strict'", db, params=(symbol,)
    )
    quotes["complete"] = True
    for h in (1, 4, 24):
        subset = outcomes[(outcomes.horizon == h) & (outcomes.status == "complete")].set_index(
            "decision_ms"
        )
        for col in (
            "gross_bp",
            "stress_bp",
            "net_bp",
            "funding_bp",
            "intraminute_bp",
            "after_close_bp",
            "exit_ms",
        ):
            quotes[f"{col}_{h}"] = quotes.decision_ms.map(subset[col])
        quotes["complete"] &= quotes[f"gross_bp_{h}"].notna()
    # Reconcile final funding observations, including any late-published records.
    funding = pd.read_sql_query(
        "SELECT ts,rate,mark FROM funding WHERE symbol=? ORDER BY ts", db, params=(symbol,)
    )
    ft = funding.ts.to_numpy(dtype=np.int64)
    cumulative = np.concatenate(
        ([0.0], (funding.rate.astype(float) * funding.mark.astype(float)).cumsum().to_numpy())
    )
    entry_prices = quotes.price.astype(float).to_numpy()
    entry = np.searchsorted(ft, quotes.ts.to_numpy(dtype=np.int64), side="right")
    for h in (1, 4, 24):
        ok = quotes.complete.to_numpy()
        exit_times = quotes.loc[ok, f"exit_ms_{h}"].to_numpy(dtype=np.int64)
        end = np.searchsorted(ft, exit_times, side="right")
        amount = (cumulative[end] - cumulative[entry[ok]]) / entry_prices[ok] * 1e4
        delta = amount - quotes.loc[ok, f"funding_bp_{h}"].to_numpy()
        quotes.loc[ok, f"funding_revision_bp_{h}"] = delta
        quotes.loc[ok, f"stress_bp_{h}"] -= delta
        quotes.loc[ok, f"net_bp_{h}"] -= delta
        quotes.loc[ok, f"funding_bp_{h}"] = amount
    quotes = calendar(quotes)
    bars = pd.read_sql_query(
        "SELECT * FROM bars WHERE symbol=? ORDER BY end_ms", db, params=(symbol,)
    )
    bars["ts"] = bars.end_ms
    returns = np.log(bars.close).diff()
    bars["vol"] = returns.rolling(1440).std(ddof=0)
    bars.loc[bars.end_ms.diff(1440) != DAY, "vol"] = np.nan
    bars["complete"] = True
    for h in (1, 4, 24):
        n = h * 60
        valid = bars.end_ms.shift(-n) - bars.end_ms == h * HOUR
        bars["complete"] &= valid
        bars[f"gross_bp_{h}"] = (bars.close.shift(-n) / bars.close - 1).where(valid) * 1e4
    bars = calendar(bars)
    bars = bars[
        bars.complete
        & bars.vol.gt(0)
        & bars.ts.ge(manifest["start_ms"])
        & bars.ts.lt(manifest["end_ms"])
        & (bars.ts % (5 * MINUTE) == 0)
    ]
    return quotes, bars


def decide(summary, counts):
    quality = (
        all(
            c["common_events"] >= PARAMETERS["minimum_events"]
            and c["active_days"] >= PARAMETERS["minimum_days"]
            and c["quote_coverage"] >= PARAMETERS["minimum_coverage"]
            and c["match_fraction"] >= PARAMETERS["minimum_matched"]
            for c in counts
            if c["spacing_hours"] == 1
        )
        and len([c for c in counts if c["spacing_hours"] == 1]) == 2
    )
    passed = []
    for h in (1, 4, 24):
        main = [s for s in summary if s["spacing_hours"] == 1 and s["horizon_hours"] == h]
        sensitivity = [s for s in summary if s["spacing_hours"] == 24 and s["horizon_hours"] == h]
        if (
            quality
            and len(main) == len(sensitivity) == 6
            and all(s["lower"] is not None and s["lower"] > 0 for s in main)
            and all(s["mean"] is not None and s["mean"] > 0 for s in sensitivity)
        ):
            passed.append(h)
    return {
        "quality_gate_passed": quality,
        "passed_horizons": passed,
        "research_signal_supported": len(passed) >= 2,
        "decision": "值得下一阶段验证"
        if len(passed) >= 2
        else "未达到信号证据门槛"
        if quality
        else "证据不足：样本或覆盖未达标",
        "live_trading_authorized": False,
    }


def evaluate(root):
    root = Path(root)
    manifest = verify(root)
    # A manual invocation cannot bring the assessment forward.
    now = int(time.time() * 1000)
    if now < manifest["review_ms"]:
        result = {
            "state": "pending",
            "due_utc": utc(manifest["review_ms"]),
            "decision": "尚未到期，不提前判定盈利",
        }
        write_json(root / "review.json", result)
        return result
    db = connect(root)
    output = root / "assessment"
    output.mkdir(exist_ok=True)
    summaries, counts = [], []
    expected = (manifest["end_ms"] - manifest["start_ms"]) // (5 * MINUTE)
    for symbol in SYMBOLS:
        all_events, controls = common_frame(db, symbol, manifest)
        all_events.to_csv(output / f"{symbol}_all_strict_events.csv", index_label="match_index")
        controls.to_csv(output / f"{symbol}_ordinary_controls.csv", index_label="match_index")
        prospective = db.execute(
            "SELECT COUNT(*) FROM quotes WHERE symbol=? AND prospective=1 AND decision_ms>=? AND decision_ms<?",
            (symbol, manifest["start_ms"], manifest["end_ms"]),
        ).fetchone()[0]
        pool = all_events[all_events.complete & all_events.vol.gt(0)]
        eligible = all_events[all_events.filter_ok.astype(bool)]
        for spacing in (1, 24):
            selected = spaced(eligible, spacing)
            selected = selected[selected.complete & selected.vol.gt(0)]
            market_matches = matched(selected, controls, PARAMETERS["seed"] + spacing)
            touch_matches = matched(selected, pool, PARAMETERS["seed"] + spacing + 100)
            common = sorted(set(market_matches) & set(touch_matches))
            paired = selected.loc[common].copy()
            counts.append(
                {
                    "symbol": symbol,
                    "spacing_hours": spacing,
                    "raw_eligible": len(eligible),
                    "complete_events": len(selected),
                    "common_events": len(common),
                    "active_days": int((paired.ts // DAY).nunique()),
                    "quote_coverage": prospective / expected,
                    "match_fraction": len(common) / len(selected) if len(selected) else 0,
                }
            )
            for h in (1, 4, 24):
                for name, matches, frame in (
                    ("market_edge", market_matches, controls),
                    ("touch_edge", touch_matches, pool),
                ):
                    paired[f"{name}_{h}"] = [
                        float(paired.loc[i, f"gross_bp_{h}"])
                        - frame.loc[matches[i], f"gross_bp_{h}"].mean()
                        for i in common
                    ]
                for statistic, col in (
                    ("stress_net", f"stress_bp_{h}"),
                    ("market_edge", f"market_edge_{h}"),
                    ("touch_edge", f"touch_edge_{h}"),
                ):
                    lo, hi = bounds(
                        paired.ts.to_numpy(),
                        paired[col].to_numpy(),
                        manifest["start_ms"],
                        manifest["end_ms"],
                    )
                    summaries.append(
                        {
                            "symbol": symbol,
                            "spacing_hours": spacing,
                            "horizon_hours": h,
                            "statistic": statistic,
                            "events": len(paired),
                            "mean": float(paired[col].mean()) if len(paired) else None,
                            "lower": lo,
                            "upper": hi,
                        }
                    )
            paired.to_csv(
                output / f"{symbol}_spacing{spacing}_paired.csv", index_label="match_index"
            )
            write_json(
                output / f"{symbol}_spacing{spacing}_matches.json",
                {
                    "market": {str(i): market_matches[i].tolist() for i in common},
                    "touch": {str(i): touch_matches[i].tolist() for i in common},
                },
            )
    result = {
        "completed_at": utc(now),
        "counts": counts,
        "summary": summaries,
        **decide(summaries, counts),
    }
    pd.DataFrame(summaries).to_csv(output / "summary.csv", index=False)
    write_json(root / "review.json", result)
    table = pd.DataFrame(summaries).to_html(index=False, float_format=lambda x: f"{x:.3f}")
    (root / "review.html").write_text(
        "<!doctype html><html lang='zh-CN'><meta charset='utf-8'><title>前向观察到期评估</title><style>body{font:16px/1.7 system-ui;margin:30px}table{border-collapse:collapse}td,th{padding:8px;border:1px solid #ddd}</style><h1>"
        + html.escape(result["decision"])
        + "</h1><p>这是报价触价代理的前向观察，不能保证实际排队成交，也不自动授权实盘。三个窗口同时报告，区间按冻结规则作多重比较调整。</p>"
        + pd.DataFrame(counts).to_html(index=False)
        + table
        + "<p><a href='status.html'>采集状态</a> · <a href='review.json'>完整结果</a></p></html>"
    )
    db.close()
    return result
