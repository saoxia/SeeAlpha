"""Public signal opportunities, independent of position/exits, using production indicators.

MRL retains the first touched quote per UTC hour and band multiplier. This is a
declustered opportunity study, not a fill simulator. All future labels are separate.
"""

import json
from collections import deque

import numpy as np
import pandas as pd

from strategy_python._indicator.bars import CompletedBar
from strategy_python._optimization.mechanism_20260915.data import ROOT, load_minutes
from strategy_python.btc_mean_reversion_long_5min.indicators import RollingBollinger, RollingMean
from strategy_python.btc_mean_reversion_long_5min.risk import DirectionalRiskModel, RiskParameters
from strategy_python.btc_pullback_short_1h.indicators import PullbackIndicators
from strategy_python.btc_trend_following_short_5min.indicators import (
    DailyBollEntryFilter,
    HourlyTrendModel,
    MaCrossModel,
)

MINUTE = 60_000_000_000
HOUR = 60 * MINUTE
DAY = 24 * HOUR


def completed(ts, row, minutes):
    end = int(ts.value)
    return CompletedBar(end - minutes * MINUTE, end, *map(float, row))


def extract(asset):
    minute = load_minutes(asset)
    five = pd.read_parquet(ROOT / f"{asset}_5m.parquet")
    higher = {
        n: {
            int(t.value): completed(t, row, n)
            for t, row in pd.read_parquet(ROOT / f"{asset}_{n}m.parquet").iterrows()
        }
        for n in (15, 60, 1440)
    }
    cross = MaCrossModel(52, 99)
    hourly = HourlyTrendModel(20, 6, use_slope_change=True, max_adverse_ma_return=0.001)
    daily = DailyBollEntryFilter(20, 2, source_seconds=300)
    pullback = PullbackIndicators()
    risk = DirectionalRiskModel(RiskParameters())
    bands = {k: RollingBollinger(20, k) for k in (2.0, 2.25)}
    ma99 = RollingMean(99)
    adverse = deque(maxlen=54)
    last_mrl = {}
    minute_ns = minute.index.as_unit("ns").asi8
    prices = minute.to_numpy()
    rows = []
    atr_fraction = np.nan
    for i, (ts, row) in enumerate(five.iterrows()):
        now = int(ts.value)
        _o, _h, l, c = map(float, row)
        daily.update(now - 1_000_000, l, c)
        daybar, hourbar = higher[1440].get(now), higher[60].get(now)
        if daybar is not None:
            pullback.update_daily(daybar)
        if hourbar is not None:
            hourly.update(hourbar.close)
            feat = pullback.update_hour(hourbar)
            atr_fraction = feat.atr / feat.close if feat is not None else np.nan
            if (
                feat is not None
                and feat.previous_close >= feat.previous_ma
                and feat.close < feat.ma
            ):
                rows.append(
                    {
                        "asset": asset,
                        "family": "PBS",
                        "variant": "shared",
                        "signal_ns": now,
                        "entry_ns": now,
                        "price": c,
                        "direction": -1,
                        "eligible": feat.down_daily,
                        "atr_fraction": atr_fraction,
                        "structure_fraction": feat.swing_high / c - 1,
                        "buffered_stop_fraction": (feat.swing_high + 0.25 * feat.atr) / c - 1,
                    }
                )
        side = cross.update(c)
        if side and i >= 150:
            eligible = hourly.direction == 1 if side == 1 else hourly.initialized and daily.allowed
            rows.append(
                {
                    "asset": asset,
                    "family": "TFL" if side == 1 else "TFS",
                    "variant": "shared",
                    "signal_ns": now,
                    "entry_ns": now,
                    "price": c,
                    "direction": side,
                    "eligible": eligible,
                    "atr_fraction": atr_fraction,
                    "hourly_direction": hourly.direction,
                    "daily_allowed": daily.allowed,
                    "hourly_ma_return": hourly.last_ma_return,
                }
            )
        # Production MRL updates its 5m decision BEFORE same-boundary 15m/hour features.
        ratio = sum(adverse) / 54 if len(adverse) == 54 else np.nan
        for k, model in bands.items():
            value = model.update(c)
            if value is None or not np.isfinite(ratio) or ratio > 0.65 + 1e-12:
                continue
            bucket = now // HOUR
            if last_mrl.get(k) == bucket:
                continue
            start = minute_ns.searchsorted(now, side="right")
            future = prices[start : start + 5]
            if len(future) != 5 or minute_ns[start + 4] != now + 5 * MINUTE:
                continue
            hits = np.flatnonzero(future[:, 2] <= value[0])
            if not len(hits):
                continue
            offset = int(hits[0])
            entry = min(float(future[offset, 0]), value[0])
            context = risk.cycle_context(1, value[0], now)
            # Risk context is measured at quote time, never at the future fill time.
            rows.append(
                dict(
                    asset=asset,
                    family="MRL",
                    variant=str(k),
                    signal_ns=now,
                    entry_ns=int(minute_ns[start + offset]),
                    price=entry,
                    quote_price=value[0],
                    direction=1,
                    eligible=context is not None,
                    atr_fraction=atr_fraction,
                    adverse_ratio=ratio,
                    **(
                        {f"risk_{name}": value for name, value in context.items()}
                        if context
                        else {}
                    ),
                )
            )
            last_mrl[k] = bucket
        fifteen = higher[15].get(now)
        if fifteen is not None:
            mean = ma99.update(fifteen.close)
            if mean is not None:
                adverse.append(fifteen.low < mean)
        if hourbar is not None:
            risk.on_hourly_bar(hourbar)
        if i % 100000 == 0:
            print("EVENTS", asset, i, len(rows), flush=True)
    result = pd.DataFrame(rows)
    result = result[result.signal_ns >= pd.Timestamp("2021-01-01", tz="UTC").value]
    result.to_parquet(ROOT / f"{asset}_events.parquet", index=False)
    print("EVENTS_DONE", asset, len(result), flush=True)


def label(asset):
    minute = load_minutes(asset)
    events = pd.read_parquet(ROOT / f"{asset}_events.parquet")
    ns = minute.index.as_unit("ns").asi8
    high, low, close = (minute[name].to_numpy() for name in ("high", "low", "close"))
    # Cumulative missing intervals makes path validity an O(1) check per label.
    gaps = np.r_[0, np.cumsum(np.diff(ns) != MINUTE)]
    rows, rejected = [], 0
    for event in events.to_dict("records"):
        p, side, start_ns = event["price"], event["direction"], event["entry_ns"]
        start = ns.searchsorted(start_ns, side="right")
        year = pd.Timestamp(start_ns - 1, tz="UTC").year
        split_end = pd.Timestamp(
            "2024-01-01" if year <= 2023 else "2025-01-01" if year == 2024 else "2026-08-31",
            tz="UTC",
        ).value
        if year < 2021:
            continue
        for hours in (6, 24, 48):
            end = start + hours * 60
            if (
                end > len(ns)
                or ns[end - 1] > split_end
                or ns[start] != start_ns + MINUTE
                or gaps[end - 1] != gaps[start]
            ):
                rejected += 1
                continue
            favorable = (high[start:end] / p - 1) if side == 1 else (1 - low[start:end] / p)
            adverse = (1 - low[start:end] / p) if side == 1 else (high[start:end] / p - 1)
            closes = side * (close[start:end] / p - 1)
            peak_i = int(np.argmax(favorable))
            record = {
                **event,
                "year": year,
                "month": pd.Timestamp(start_ns - 1, tz="UTC").strftime("%Y-%m"),
                "split": "train" if year <= 2023 else "validation" if year == 2024 else "review",
                "hours": hours,
                "label_end_ns": int(ns[end - 1]),
                "mfe": max(0.0, float(favorable.max())),
                "mae": max(0.0, float(adverse.max())),
                "terminal_return": float(closes[-1]),
                "peak_minutes": peak_i + 1,
                "mae_before_peak": max(0.0, float(adverse[: peak_i + 1].max())),
            }
            for target in (0.005, 0.01, 0.02, 0.03, 0.04):
                hits = np.flatnonzero(favorable >= target)
                name = str(target)
                record[f"hit_{name}"] = bool(len(hits))
                # Exclude the target-hit minute: high/low order inside it is unknown.
                record[f"mae_before_{name}"] = (
                    max(0.0, float(adverse[: hits[0]].max()))
                    if len(hits) and hits[0] > 0
                    else 0.0
                    if len(hits)
                    else np.nan
                )
                record[f"minutes_to_{name}"] = int(hits[0]) + 1 if len(hits) else np.nan
            rows.append(record)
    out = pd.DataFrame(rows)
    out.to_parquet(ROOT / f"{asset}_paths.parquet", index=False)
    summary = (
        out.groupby(["family", "variant", "eligible", "split", "hours"])
        .size()
        .rename("count")
        .reset_index()
    )
    summary.to_csv(ROOT / f"{asset}_coverage.csv", index=False)
    (ROOT / f"{asset}_label_audit.json").write_text(
        json.dumps(
            {
                "events": len(events),
                "valid_labels": len(out),
                "rejected_horizon_labels": rejected,
                "no_truncated_trade_paths": True,
            },
            indent=2,
        )
        + "\n"
    )
    print("LABELS_DONE", asset, len(out), rejected, flush=True)


if __name__ == "__main__":
    for asset in ("ETH", "BTC"):
        extract(asset)
        label(asset)
