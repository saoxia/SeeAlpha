"""Causal signals and conservative minute OHLC exits, isolated from existing strategies."""

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class Config:
    name: str = "time48"
    hours: int = 48
    stop: str = "none"
    atr_multiple: float = 2.0
    reward: float = 0.0


CONFIGS = [
    Config(),
    Config("structure24", 24, "structure"),
    Config("structure48", 48, "structure"),
    Config("structure72", 72, "structure"),
    Config("atr1.5", 48, "atr", 1.5),
    Config("atr2", 48, "atr", 2.0),
    Config("structure_target2", 48, "structure", reward=2.0),
]


def signals(hourly):
    h = hourly.reindex(pd.date_range(hourly.index.min(), hourly.index.max(), freq="h"))
    prev = h.close.shift()
    tr = pd.concat([h.high - h.low, (h.high - prev).abs(), (h.low - prev).abs()], axis=1).max(
        axis=1
    )
    atr = tr.rolling(24).mean()
    floor = h.low.shift().rolling(24).min()
    valid = h.close.notna().rolling(193).sum().eq(193)
    signal = (h.low < floor) & (h.close > floor) & valid & atr.notna()
    return pd.DataFrame({"low": h.low, "atr": atr}, index=h.index).loc[signal]


def find_exit(opens, highs, lows, times, entry, deadline, stop, target, boundary):
    """Return index, executable price, reason; prior bars only determine entry levels."""
    end = min(int(np.searchsorted(times, deadline)), boundary - 1)
    idx = np.arange(entry, end + 1)
    hits = np.zeros(len(idx), dtype=bool)
    if np.isfinite(stop):
        hits |= lows[idx] <= stop
    if np.isfinite(target):
        hits |= highs[idx] >= target
    gaps = np.flatnonzero(np.diff(times[entry : end + 1]) != 60_000_000_000) + entry + 1
    first_gap = int(gaps[0]) if len(gaps) else end + 1
    found = np.flatnonzero(hits)
    first_hit = int(idx[found[0]]) if len(found) else end + 1
    i = min(end, first_gap, first_hit)
    if i == first_gap:
        return i, float(opens[i]), "data_gap"
    if i == end:
        return i, float(opens[i]), "time" if times[i] >= deadline else "boundary"
    if np.isfinite(stop) and lows[i] <= stop:
        return i, float(min(opens[i], stop)), "stop"
    return i, float(target), "target"


def simulate(minute, signal, config, start, end, *, mode="fixed", cap=1.0, risk=0.0, fee=0.0005):
    times = minute.index.as_unit("ns").asi8
    o, hi, lo, cl = (minute[k].to_numpy() for k in ["open", "high", "low", "close"])
    first = int(np.searchsorted(times, start.value + 60_000_000_000))
    boundary = int(np.searchsorted(times, end.value + 60_000_000_000))
    boundary = min(boundary, len(times))
    eq = np.full(boundary - first, 500.0)
    balance, last, cursor = 500.0, first, first
    trades, rejected = [], 0
    for t, s in signal.loc[(signal.index >= start) & (signal.index < end)].iterrows():
        # Minute labels are interval ends; t+2m row opens at t+1m.
        i = int(np.searchsorted(times, t.value + 120_000_000_000))
        if i >= boundary or i < last:
            continue
        if times[i] != t.value + 120_000_000_000:
            rejected += 1
            continue
        px = float(o[i])
        stop = (
            s.low - 0.1 * s.atr
            if config.stop == "structure"
            else (px - config.atr_multiple * s.atr if config.stop == "atr" else np.nan)
        )
        if np.isfinite(stop) and (stop <= 0 or stop >= px):
            rejected += 1
            continue
        target = px + config.reward * (px - stop) if config.reward else np.nan
        deadline = times[i] + config.hours * 3_600_000_000_000
        j, exit_px, reason = find_exit(o, hi, lo, times, i, deadline, stop, target, boundary)
        if mode == "fixed":
            qty = 500 / px
        else:
            notional = max(0.0, balance) * cap / (1 + fee)
            qty = notional / px
            if risk:
                assert np.isfinite(stop)
                qty = min(qty, max(0.0, balance) * risk / (px - stop + fee * (px + stop)))
            qty = np.floor(qty / 0.001 + 1e-12) * 0.001
            if qty < 0.001 or qty * px < 50:
                rejected += 1
                continue
        eq[cursor - first : i - first] = balance
        entry_fee, exit_fee = qty * px * fee, qty * exit_px * fee
        eq[i - first : j - first] = balance - entry_fee + qty * (cl[i:j] - px)
        pnl = qty * (exit_px - px) - entry_fee - exit_fee
        balance += pnl
        eq[j - first] = balance
        trades.append(
            {
                "signal_time": t,
                "entry_time": pd.Timestamp(times[i] - 60_000_000_000, tz="UTC"),
                "exit_time": pd.Timestamp(
                    times[j] - (0 if reason in ("stop", "target") else 60_000_000_000), tz="UTC"
                ),
                "qty": qty,
                "entry": px,
                "exit": exit_px,
                "stop": stop,
                "target": target,
                "pnl": pnl,
                "fees": entry_fee + exit_fee,
                "reason": reason,
            }
        )
        # Close then reopen at the same minute open, matching the entry-event study.
        last = j if reason in ("time", "boundary", "data_gap") else j + 1
        cursor = j + 1
    eq[cursor - first :] = balance
    marked = pd.Series(eq, index=minute.index[first:boundary] - pd.Timedelta(nanoseconds=1))
    peak = np.maximum.accumulate(np.r_[500.0, eq])[1:]
    dd = float(np.max((peak - eq) / peak) * 100)
    tr = pd.DataFrame(trades)
    wins = tr.loc[tr.pnl > 0, "pnl"] if len(tr) else pd.Series(dtype=float)
    losses = tr.loc[tr.pnl < 0, "pnl"] if len(tr) else pd.Series(dtype=float)
    metrics = {
        "net_pnl": balance - 500,
        "return_pct": (balance / 500 - 1) * 100,
        "max_drawdown_pct": dd,
        "trades": len(tr),
        "fees": float(tr.fees.sum()) if len(tr) else 0,
        "win_rate_pct": len(wins) / len(tr) * 100 if len(tr) else 0,
        "payoff": float(wins.mean() / -losses.mean()) if len(wins) and len(losses) else None,
        "rejected_entries": rejected,
        "gap_exits": int((tr.reason == "data_gap").sum()) if len(tr) else 0,
        "active_months": tr.entry_time.dt.strftime("%Y-%m").nunique() if len(tr) else 0,
    }
    assert abs(sum(x["pnl"] for x in trades) - (balance - 500)) < 1e-7
    return metrics, tr, marked.resample("D").last().dropna()
