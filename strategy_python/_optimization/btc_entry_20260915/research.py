"""Finite BTC entry-event study. All features are known before entry."""

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
DATA = ROOT.parent / "mechanism_20260915"
START = pd.Timestamp("2021-01-01", tz="UTC")
END = pd.Timestamp("2026-08-31", tz="UTC")
PERIODS = {
    "develop": (START, pd.Timestamp("2024-01-01", tz="UTC")),
    "validation": (pd.Timestamp("2024-01-01", tz="UTC"), pd.Timestamp("2025-01-01", tz="UTC")),
    "review": (pd.Timestamp("2025-01-01", tz="UTC"), END),
}
NAMES = {
    "breakout_long": "日通道向上突破",
    "breakout_short": "日通道向下突破",
    "pullback_long": "周趋势向上、日均线回踩收复",
    "pullback_short": "周趋势向下、日均线反弹跌回",
    "reversion_long": "日尺度负向极端偏离反转",
    "reversion_short": "日尺度正向极端偏离反转",
}


def features(h):
    # Reindexing inserts missing hours; full rolling windows cannot cross gaps.
    h = h.reindex(pd.date_range(h.index.min(), h.index.max(), freq="h"))
    close = h.close
    ma24 = close.rolling(24).mean()
    ma168 = close.rolling(168).mean()
    prior_mean = close.shift().rolling(24).mean()
    prior_sd = close.shift().rolling(24).std(ddof=0)
    z = (close - prior_mean) / prior_sd.replace(0, np.nan)
    valid_history = close.notna().rolling(193).sum().eq(193)
    signals = pd.DataFrame(
        {
            "breakout_long": close > h.high.shift().rolling(24).max(),
            "breakout_short": close < h.low.shift().rolling(24).min(),
            "pullback_long": (close > ma168)
            & (ma168 > ma168.shift(24))
            & (close.shift() <= ma24.shift())
            & (h.low <= ma24)
            & (close > ma24),
            "pullback_short": (close < ma168)
            & (ma168 < ma168.shift(24))
            & (close.shift() >= ma24.shift())
            & (h.high >= ma24)
            & (close < ma24),
            "reversion_long": (z < -2) & (z.shift() >= -2),
            "reversion_short": (z > 2) & (z.shift() <= 2),
        },
        index=h.index,
    )
    return h, signals & valid_history.to_numpy()[:, None], valid_history


def select_events(frame, mask):
    rows = []
    available = START
    for idx in np.flatnonzero(mask.to_numpy()):
        row = frame.iloc[idx]
        if row.entry_time >= available:
            rows.append(idx)
            available = row.exit_time
    return frame.iloc[rows].copy()


def bootstrap(events, controls):
    months = pd.period_range("2024-01", "2026-08", freq="M").astype(str)

    def sums(d):
        d = d.copy()
        d["month"] = d.entry_time.dt.strftime("%Y-%m")
        return (
            d.groupby("month")
            .agg(net=("net", "sum"), n=("net", "size"))
            .reindex(months, fill_value=0)
        )

    a, b = sums(events), sums(controls)
    if len(events) < 2:
        return {
            "net_ci_low": np.nan,
            "net_ci_high": np.nan,
            "excess_ci_low": np.nan,
            "excess_ci_high": np.nan,
            "p_value": 1.0,
        }
    rng = np.random.default_rng(20260915)
    starts = rng.integers(0, len(months) - 2, size=(5000, 11))
    idx = (starts[:, :, None] + np.arange(3)).reshape(5000, -1)[:, : len(months)]
    an, bn = a.n.to_numpy()[idx].sum(axis=1), b.n.to_numpy()[idx].sum(axis=1)
    usable = (an > 0) & (bn > 0)
    ar = a.net.to_numpy()[idx].sum(axis=1)[usable] / an[usable]
    br = b.net.to_numpy()[idx].sum(axis=1)[usable] / bn[usable]
    difference = ar - br
    observed = events.net.mean() - controls.net.mean()
    # Centered bootstrap null, one-sided H0: no positive excess.
    p = (1 + np.count_nonzero(difference - observed >= observed)) / (len(difference) + 1)
    return {
        "net_ci_low": np.quantile(ar, 0.025),
        "net_ci_high": np.quantile(ar, 0.975),
        "excess_ci_low": np.quantile(difference, 0.025),
        "excess_ci_high": np.quantile(difference, 0.975),
        "p_value": p,
    }


def main():
    paths = [
        DATA / "BTC_60m.parquet",
        DATA / "BTC_minutes.parquet",
        ROOT / "PROTOCOL.md",
        Path(__file__),
    ]
    provenance = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    h, signals, valid_history = features(pd.read_parquet(paths[0]).sort_index())
    minute = pd.read_parquet(paths[1], columns=["open"])
    # Minute index labels END times: open at t+1 minute is in row t+2 minutes.
    entry_times = h.index + pd.Timedelta(minutes=1)
    entry = minute.open.reindex(h.index + pd.Timedelta(minutes=2)).to_numpy()
    rows, yearly, all_events, inference, exclusions = [], [], [], [], []
    review_cache = {}
    for horizon in [6, 24, 48]:
        exit_times = entry_times + pd.Timedelta(hours=horizon)
        exit_px = minute.open.reindex(exit_times + pd.Timedelta(minutes=1)).to_numpy()
        ratio = exit_px / entry
        # Include all complete hours spanning entry through exit.
        continuity = (
            h.close.notna().rolling(horizon + 1).sum().shift(-(horizon + 1)).eq(horizon + 1)
        )
        base = pd.DataFrame(
            {
                "signal_time": h.index,
                "entry_time": entry_times,
                "exit_time": exit_times,
                "ratio": ratio,
            },
            index=h.index,
        )
        valid = (
            valid_history
            & continuity
            & np.isfinite(ratio)
            & (entry_times >= START)
            & (exit_times < END)
        )
        for name in NAMES:
            side = 1 if name.endswith("long") else -1
            gross = side * (ratio - 1)
            base["gross"] = gross
            base["net"] = gross - 0.0005 * (1 + ratio)
            base["extra2bp"] = gross - 0.0007 * (1 + ratio)
            base["extra5bp"] = gross - 0.001 * (1 + ratio)
            selected_parts, control_parts = [], []
            for period, (start, end) in PERIODS.items():
                eligible = valid & (entry_times >= start) & (exit_times < end)
                control = base.loc[eligible].copy()
                mask = eligible & signals[name]
                events = select_events(base, mask)
                exclusions.append(
                    {
                        "signal": name,
                        "horizon": horizon,
                        "period": period,
                        "raw_signals": int(
                            (signals[name] & (entry_times >= start) & (entry_times < end)).sum()
                        ),
                        "valid_before_overlap": int(mask.sum()),
                        "selected": len(events),
                    }
                )
                events["signal"], events["horizon"], events["period"] = name, horizon, period
                all_events.append(events)
                losses = events.loc[events.net < 0, "net"]
                wins = events.loc[events.net > 0, "net"]
                rows.append(
                    {
                        "signal": name,
                        "horizon": horizon,
                        "period": period,
                        "n": len(events),
                        "gross_mean": events.gross.mean(),
                        "net_mean": events.net.mean(),
                        "benchmark_mean": control.net.mean(),
                        "excess": events.net.mean() - control.net.mean(),
                        "win_rate": (events.net > 0).mean(),
                        "payoff": wins.mean() / (-losses.mean()),
                        "profit_factor": wins.sum() / (-losses.sum()) if len(losses) else np.nan,
                        "extra2bp_mean": events.extra2bp.mean(),
                        "extra5bp_mean": events.extra5bp.mean(),
                    }
                )
                for year, group in events.groupby(events.entry_time.dt.year):
                    yearly.append(
                        {
                            "signal": name,
                            "horizon": horizon,
                            "year": int(year),
                            "n": len(group),
                            "net_mean": group.net.mean(),
                            "fixed_1000_notional_pnl": 1000 * group.net.sum(),
                        }
                    )
                if period != "develop":
                    selected_parts.append(events)
                    control_parts.append(control)
            ev, co = pd.concat(selected_parts), pd.concat(control_parts)
            review_cache[name, horizon] = (ev.net.mean(), ev.net.mean() - co.net.mean())
            if horizon == 24:
                inference.append(
                    dict(
                        signal=name,
                        n=len(ev),
                        net_mean=ev.net.mean(),
                        excess=ev.net.mean() - co.net.mean(),
                        **bootstrap(ev, co),
                    )
                )
    table = pd.DataFrame(rows)
    tests = pd.DataFrame(inference).sort_values("p_value")
    q = tests.p_value.to_numpy() * len(tests) / np.arange(1, len(tests) + 1)
    tests["q_value"] = np.minimum.accumulate(q[::-1])[::-1].clip(max=1)
    decisions = []
    for _, t in tests.iterrows():
        selected = table[(table.signal == t.signal) & (table.horizon == 24)].set_index("period")
        gates = {
            "all_three_periods_positive": bool((selected.net_mean > 0).all()),
            "validation_review_at_least_30": bool(
                (selected.loc[["validation", "review"], "n"] >= 30).all()
            ),
            "net_ci_positive": bool(t.net_ci_low > 0),
            "excess_ci_positive": bool(t.excess_ci_low > 0),
            "multiple_test_pass": bool(t.q_value <= 0.05),
            "time_neighbors_positive": all(
                all(x > 0 for x in review_cache[t.signal, h]) for h in [6, 48]
            ),
        }
        decisions.append({"signal": t.signal, "accepted": all(gates.values()), "gates": gates})
    table.to_csv(ROOT / "signal_results.csv", index=False)
    pd.DataFrame(yearly).to_csv(ROOT / "annual_results.csv", index=False)
    pd.concat(all_events).to_parquet(ROOT / "events.parquet", index=False)
    tests.to_csv(ROOT / "uncertainty.csv", index=False)
    (ROOT / "decisions.json").write_text(json.dumps(decisions, indent=2) + "\n")
    (ROOT / "exclusions.json").write_text(json.dumps(exclusions, indent=2) + "\n")
    (ROOT / "provenance.json").write_text(json.dumps(provenance, indent=2) + "\n")
    print(table[table.horizon == 24].to_string(index=False), flush=True)
    print(tests.to_string(index=False), flush=True)
    print(json.dumps(decisions, indent=2), flush=True)


if __name__ == "__main__":
    main()
