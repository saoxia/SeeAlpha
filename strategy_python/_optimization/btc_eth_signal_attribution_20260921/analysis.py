"""Frozen event matching and fixed-entry exit/size attribution; no parameter search."""

import ast
import json

import numpy as np
import pandas as pd

from .native import END, IDS, ROOT, START, digest, json_write, verify

MINUTE = 60_000_000_000
HOUR = 60 * MINUTE
HORIZONS = (1, 4, 24)
SEED = 20260921
OUTPUT = ROOT / "analysis"


def prepare_market(frame):
    frame = frame.copy()
    ts = frame.ts.to_numpy(dtype=np.int64)
    assert np.all(np.diff(ts) > 0) and np.all((ts + 1_000_000) % MINUTE == 0)
    log_return = np.log(frame.close).diff()
    frame["vol"] = log_return.rolling(1440, min_periods=1440).std(ddof=0)
    frame.loc[frame.ts.diff(1440) != 1440 * MINUTE, "vol"] = np.nan
    frame["complete"] = True
    for hours in HORIZONS:
        n = hours * 60
        complete = frame.ts.shift(-n) - frame.ts == n * MINUTE
        frame["complete"] &= complete
        frame[f"close_{hours}"] = frame.close.shift(-n).where(complete)
        frame[f"high_{hours}"] = (
            frame.high.shift(-1)[::-1].rolling(n, min_periods=n).max()[::-1].where(complete)
        )
        frame[f"low_{hours}"] = (
            frame.low.shift(-1)[::-1].rolling(n, min_periods=n).min()[::-1].where(complete)
        )
    dt = pd.to_datetime(frame.ts, utc=True)
    frame["month"] = dt.dt.year * 12 + dt.dt.month - 1
    frame["hour_group"] = dt.dt.hour // 6
    return frame


def indices_for(market, timestamps):
    ts = market.ts.to_numpy(dtype=np.int64)
    values = np.asarray(timestamps, dtype=np.int64)
    idx = np.searchsorted(ts, values)
    assert np.all(idx < len(ts)) and np.array_equal(ts[idx], values)
    return idx


def touches(market, quotes):
    """Each quote uses current close or strictly subsequent lows, never current low."""
    quotes = quotes[quotes.valid].copy().reset_index(drop=True)
    idx = indices_for(market, quotes.ts)
    price = quotes.price.to_numpy()
    ts = market.ts.to_numpy(dtype=np.int64)
    close = market.close.to_numpy()
    low = market.low.to_numpy()
    touched = np.where(close[idx] <= price, idx, -1)
    for step in range(1, 6):
        j = np.minimum(idx + step, len(ts) - 1)
        good = (touched < 0) & (ts[j] - ts[idx] == step * MINUTE) & (low[j] <= price)
        touched[good] = j[good]
    quotes["decision_ts"] = quotes.ts
    quotes["i"] = touched
    quotes = quotes[quotes.i >= 0].copy()
    quotes["ts"] = ts[quotes.i.to_numpy()]
    return quotes.sort_values(["ts", "decision_ts"]).reset_index(drop=True)


def spaced(frame, hours):
    keep, last = [], -(2**63)
    for i, ts in enumerate(frame.ts.to_numpy(dtype=np.int64)):
        if int(ts) - last >= hours * HOUR:
            keep.append(i)
            last = int(ts)
    return frame.iloc[keep].copy().reset_index(drop=True)


def block_interval(timestamps, values, first_month, last_month, seed=SEED):
    """3-month moving blocks; resample sums AND counts, preserving unequal activity."""
    dt = pd.to_datetime(timestamps, utc=True)
    month = np.asarray(dt.year * 12 + dt.month - 1) - first_month
    n = last_month - first_month + 1
    counts = np.bincount(month, minlength=n)
    sums = np.bincount(month, weights=np.asarray(values), minlength=n)
    rng = np.random.default_rng(seed)
    starts = rng.integers(0, n, size=(2000, (n + 2) // 3))
    blocks = ((starts[:, :, None] + np.arange(3)) % n).reshape(2000, -1)[:, :n]
    denominator = counts[blocks].sum(axis=1)
    estimates = sums[blocks].sum(axis=1)[denominator > 0] / denominator[denominator > 0]
    return np.quantile(estimates, [0.025, 0.975]).tolist(), int((counts > 0).sum())


def match_controls(market, events, pool, seed):
    rng = np.random.default_rng(seed)
    groups = {}
    valid_pool = market.loc[pool]
    for key, rows in valid_pool.groupby(["month", "hour_group"]):
        ii = rows.index.to_numpy()
        order = np.argsort(np.log(rows.vol.to_numpy()))
        groups[key] = (np.log(rows.vol.to_numpy())[order], ii[order])
    event_indices, controls = [], []
    ts = market.ts.to_numpy(dtype=np.int64)
    for i in events.i:
        row = market.loc[i]
        group = groups.get((row.month, row.hour_group))
        if group is None:
            continue
        logs, ii = group
        logvol = np.log(row.vol)
        left, right = np.searchsorted(logs, [logvol - 0.25, logvol + 0.25], side="left")
        candidates = ii[left:right]
        candidates = candidates[np.abs(ts[candidates] - ts[i]) > 24 * HOUR]
        if len(candidates):
            event_indices.append(i)
            controls.append(rng.choice(candidates, size=20, replace=True))
    return np.asarray(event_indices, dtype=int), np.asarray(controls, dtype=int).reshape(-1, 20)


def event_study(asset, kind, market):
    raw = pd.read_parquet(ROOT / "runs" / f"{asset}_{kind}" / "decisions.parquet")
    params = json.loads((ROOT / "runs" / f"{asset}_{kind}" / "parameters.json").read_text())
    taker = float(params["instrument"]["taker_fee"])
    if kind == "MRL":
        all_touches = touches(market, raw)
        candidates = all_touches[all_touches.eligible].copy()
        mechanical_pool = all_touches.i.drop_duplicates().to_numpy()
    else:
        candidates = raw.copy()
        candidates["i"] = indices_for(market, raw.ts)
        mechanical_pool = None
    direction = 1 if kind == "MRL" else -1
    viable = (
        market.complete
        & market.vol.gt(0)
        & market.vol.notna()
        & market.ts.ge(int(START.timestamp() * 1e9))
        & market.ts.lt(int(END.timestamp() * 1e9))
    )
    pools = {
        "calendar_vol": market.index[
            viable & ((market.ts + 1_000_000) % (5 * MINUTE) == 0)
        ].to_numpy()
    }
    if mechanical_pool is not None:
        pools["touch_vol"] = mechanical_pool[viable.to_numpy()[mechanical_pool]]
    counts = {
        "asset": asset,
        "strategy": kind,
        "decisions": len(raw),
        "market_eligible_decisions": int(raw.eligible.sum()),
        "eligible_decision_status": raw[raw.eligible].status.value_counts().to_dict(),
        "all_quote_touch_rows": len(all_touches) if kind == "MRL" else None,
        "eligible_candidates_before_dedup": len(candidates),
        "cohorts": [],
    }
    candidates.to_parquet(OUTPUT / f"{asset}_{kind}_candidates.parquet", index=False)
    summaries, annual, matching_files = [], [], []
    for spacing in (1, 24):
        unique = spaced(candidates, spacing)
        available = unique[viable.to_numpy()[unique.i]].copy()
        counts["cohorts"].append(
            {
                "spacing_hours": spacing,
                "deduplicated": len(unique),
                "common_window_and_past_vol_available": len(available),
                "missing_window_or_vol": len(unique) - len(available),
            }
        )
        for pool_name, pool in pools.items():
            ei, ci = match_controls(
                market,
                available,
                pool,
                SEED
                + spacing
                + (kind == "TFS")
                + 100 * (asset == "ETH")
                + 1000 * (pool_name == "touch_vol"),
            )
            record = pd.DataFrame({"ts": market.ts.to_numpy()[ei], "i": ei})
            for h in HORIZONS:
                returns = (
                    direction
                    * (market[f"close_{h}"].to_numpy() / market.close.to_numpy() - 1)
                    * 1e4
                )
                highs = market[f"high_{h}"].to_numpy() / market.close.to_numpy() - 1
                lows = market[f"low_{h}"].to_numpy() / market.close.to_numpy() - 1
                record[f"return_{h}_bp"] = returns[ei]
                ratio = market[f"close_{h}"].to_numpy()[ei] / market.close.to_numpy()[ei]
                record[f"market_net_{h}_bp"] = returns[ei] - taker * (1 + ratio) * 1e4
                record[f"market_stress_{h}_bp"] = returns[ei] - (taker + 0.0005) * (1 + ratio) * 1e4
                record[f"control_{h}_bp"] = returns[ci].mean(axis=1)
                record[f"edge_{h}_bp"] = record[f"return_{h}_bp"] - record[f"control_{h}_bp"]
                record[f"mfe_{h}_bp"] = (
                    np.maximum(0, highs[ei] if direction > 0 else -lows[ei]) * 1e4
                )
                record[f"mae_{h}_bp"] = (
                    np.minimum(0, lows[ei] if direction > 0 else -highs[ei]) * 1e4
                )
            stem = f"{asset}_{kind}_s{spacing}_{pool_name}"
            record.to_parquet(OUTPUT / (stem + ".parquet"), index=False)
            np.savez_compressed(OUTPUT / (stem + "_matches.npz"), events=ei, controls=ci)
            matching_files.append(stem)
            for period in ("full", "2025+"):
                selected = (
                    record
                    if period == "full"
                    else record[record.ts >= pd.Timestamp("2025-01-01", tz="UTC").value]
                )
                for h in HORIZONS:
                    ci95, months = (
                        block_interval(
                            selected.ts.to_numpy(),
                            selected[f"edge_{h}_bp"],
                            (2021 if period == "full" else 2025) * 12,
                            2026 * 12 + 7,
                        )
                        if len(selected)
                        else ([None, None], 0)
                    )
                    summaries.append(
                        {
                            "asset": asset,
                            "strategy": kind,
                            "spacing_hours": spacing,
                            "control": pool_name,
                            "period": period,
                            "horizon_hours": h,
                            "events": len(selected),
                            "active_months": months,
                            "unmatched_full_cohort": len(available) - len(record),
                            "return_bp": selected[f"return_{h}_bp"].mean(),
                            "market_net_bp": selected[f"market_net_{h}_bp"].mean(),
                            "market_stress_bp": selected[f"market_stress_{h}_bp"].mean(),
                            "control_bp": selected[f"control_{h}_bp"].mean(),
                            "edge_bp": selected[f"edge_{h}_bp"].mean(),
                            "edge_low95_bp": ci95[0],
                            "edge_high95_bp": ci95[1],
                            "mfe_bp": selected[f"mfe_{h}_bp"].mean(),
                            "mae_bp": selected[f"mae_{h}_bp"].mean(),
                            "sufficient_count": len(selected) >= 30 and months >= 6,
                        }
                    )
            record["year"] = pd.to_datetime(record.ts, utc=True).dt.year
            for year, group in record.groupby("year"):
                for h in HORIZONS:
                    annual.append(
                        {
                            "asset": asset,
                            "strategy": kind,
                            "spacing_hours": spacing,
                            "control": pool_name,
                            "year": year,
                            "horizon_hours": h,
                            "events": len(group),
                            "return_bp": group[f"return_{h}_bp"].mean(),
                            "edge_bp": group[f"edge_{h}_bp"].mean(),
                        }
                    )
    counts["matching_files"] = matching_files
    return summaries, annual, counts


def fee_value(value):
    return sum(float(x.split()[0]) for x in ast.literal_eval(value))


def trade_attribution(asset, kind, market):
    run = ROOT / "runs" / f"{asset}_{kind}"
    trades = pd.read_csv(run / "trades.csv")
    fills = pd.read_csv(run / "fills.csv")
    fills["fee"] = fills.commissions.map(fee_value)
    params = json.loads((run / "parameters.json").read_text())
    taker = float(params["instrument"]["taker_fee"])
    direction = 1 if kind == "MRL" else -1
    results = []
    for trade in trades.itertuples():
        ff = fills[fills.position_id == trade.id]
        entry = ff[~ff.is_reduce_only.astype(bool)]
        exits = ff[ff.is_reduce_only.astype(bool)]
        assert len(entry) == 1 and len(exits) >= 1
        en = entry.iloc[0]
        quantity, price = float(en.filled_qty), float(en.avg_px)
        notional = quantity * price
        assert abs(float(exits.filled_qty.sum()) - quantity) < 1e-9
        gross = direction * (float((exits.avg_px * exits.filled_qty).sum()) - notional)
        assert abs(gross - ff.fee.sum() - trade.pnl) < 1e-7
        entry_ts, exit_ts = (
            pd.Timestamp(trade.entry_time).value,
            pd.Timestamp(trade.exit_time).value,
        )
        i = int(indices_for(market, [entry_ts])[0])
        j = int(indices_for(market, [exit_ts])[0])
        path = market.iloc[i + 1 : j + 1]
        path_complete = (exit_ts - entry_ts == (j - i) * MINUTE) and j > i
        tags = " ".join(exits.tags.astype(str))
        role = (
            "take_profit"
            if "take-profit" in tags
            else "stop"
            if "stop" in tags
            else "market_exit_or_end"
        )
        hold = (exit_ts - entry_ts) / HOUR
        row = {
            "id": trade.id,
            "entry_ts": entry_ts,
            "exit_ts": exit_ts,
            "entry_time": trade.entry_time,
            "exit_time": trade.exit_time,
            "quantity": quantity,
            "entry_price": price,
            "entry_notional": notional,
            "net_pnl": trade.pnl,
            "gross_pnl": gross,
            "fees": float(ff.fee.sum()),
            "outcome": "win" if trade.pnl > 0 else "loss" if trade.pnl < 0 else "flat",
            "exit_role": role,
            "holding_hours": hold,
            "holding_group": "<=4h"
            if hold <= 4
            else "4-24h"
            if hold <= 24
            else "1-7d"
            if hold <= 168
            else ">7d",
            "original_net_bp": trade.pnl / notional * 1e4,
            "original_gross_bp": gross / notional * 1e4,
            "original_fee_bp": float(ff.fee.sum()) / notional * 1e4,
            "original_turnover_ratio": float(trade.notional) / notional,
            "original_stress_bp": (trade.pnl - float(trade.notional) * 0.0005) / notional * 1e4,
            "path_complete": path_complete,
            "same_minute_exit": j == i,
            "mfe_bp": max(
                0, path.high.max() / price - 1 if direction > 0 else 1 - path.low.min() / price
            )
            * 1e4
            if path_complete
            else np.nan,
            "mae_bp": min(
                0, path.low.min() / price - 1 if direction > 0 else 1 - path.high.max() / price
            )
            * 1e4
            if path_complete
            else np.nan,
            "timed_complete": bool(market.loc[i, "complete"]),
        }
        for h in HORIZONS:
            future = float(market.loc[i, f"close_{h}"])
            g = direction * (future / price - 1)
            fee = float(en.fee) / notional + taker * future / price
            row[f"time{h}_net_bp"] = (g - fee) * 1e4
            row[f"time{h}_gross_bp"] = g * 1e4
            row[f"time{h}_stress_bp"] = (g - fee - 0.0005 * (1 + future / price)) * 1e4
        results.append(row)
    frame = pd.DataFrame(results)
    frame.to_csv(OUTPUT / f"{asset}_{kind}_all_trades.csv", index=False)
    frame[frame.outcome == "loss"].to_csv(OUTPUT / f"{asset}_{kind}_all_losses.csv", index=False)
    rows = []
    for split in ("outcome", "holding_group", "exit_role"):
        for value, group in frame.groupby(split):
            rows.append(
                {
                    "asset": asset,
                    "strategy": kind,
                    "split": split,
                    "group": value,
                    "trades": len(group),
                    "net_pnl": group.net_pnl.sum(),
                    "gross_pnl": group.gross_pnl.sum(),
                    "fees": group.fees.sum(),
                    "mean_net_bp": group.original_net_bp.mean(),
                    "mean_holding_hours": group.holding_hours.mean(),
                    "sum_holding_hours": group.holding_hours.sum(),
                    "mean_entry_notional": group.entry_notional.mean(),
                    "mean_mfe_bp": group.mfe_bp.mean(),
                    "mean_mae_bp": group.mae_bp.mean(),
                    "complete_paths": int(group.path_complete.sum()),
                }
            )
    comparisons = []
    cohort = frame[frame.timed_complete].copy()
    for period in ("full", "2025+"):
        common = (
            cohort
            if period == "full"
            else cohort[cohort.entry_ts >= pd.Timestamp("2025-01-01", tz="UTC").value]
        )
        for exit_name in ("original", "time1", "time4", "time24"):
            for stress in (False, True):
                values = common[f"{exit_name}_{'stress' if stress else 'net'}_bp"]
                comparisons.append(
                    {
                        "asset": asset,
                        "strategy": kind,
                        "period": period,
                        "exit": exit_name,
                        "extra_fee_bp_per_side": 5 if stress else 0,
                        "trades": len(common),
                        "all_trade_cohort_excluded": len(frame) - len(cohort),
                        "mean_net_bp": values.mean(),
                        "fixed100_sum_pnl": values.sum() / 100,
                        "original_size_sum_pnl": float(
                            (values / 1e4 * common.entry_notional).sum()
                        ),
                        "win_pct": float((values > 0).mean() * 100),
                        "gross_mean_bp": common[f"{exit_name}_gross_bp"].mean(),
                    }
                )
    wins, losses = frame[frame.net_pnl > 0], frame[frame.net_pnl < 0]
    stats = {
        "asset": asset,
        "strategy": kind,
        "trades": len(frame),
        "wins": len(wins),
        "losses": len(losses),
        "net_pnl": float(frame.net_pnl.sum()),
        "gross_pnl": float(frame.gross_pnl.sum()),
        "fees": float(frame.fees.sum()),
        "win_total": float(wins.net_pnl.sum()),
        "loss_total": float(losses.net_pnl.sum()),
        "avg_win": float(wins.net_pnl.mean()),
        "avg_loss": float(losses.net_pnl.mean()),
        "max_holding_days": float(frame.holding_hours.max() / 24),
        "position_time_pct": float(
            frame.holding_hours.sum() / ((END - START).total_seconds() / 3600) * 100
        ),
        "median_tp_bp": float(
            frame.loc[frame.exit_role == "take_profit", "original_gross_bp"].median()
        )
        if (frame.exit_role == "take_profit").any()
        else None,
        "excluded_timed_trades": len(frame) - len(cohort),
        "all_gaps_or_same_minute_path_exclusions": int((~frame.path_complete).sum()),
    }
    return rows, comparisons, stats


def decision(summary):
    result = {}
    for kind in ("MRL", "TFS"):
        selected = summary[summary.strategy == kind]
        passed = []
        expected = 16 if kind == "MRL" else 8  # markets x periods x spacings x control types
        for h in HORIZONS:
            group = selected[selected.horizon_hours == h]
            if (
                len(group) == expected
                and (group.sufficient_count & group.edge_low95_bp.gt(0)).all()
            ):
                passed.append(h)
        result[kind] = {
            "common_passed_horizons": passed,
            "signal_evidence_gate_passed": len(passed) >= 2,
            "out_of_sample": False,
            "live_approval": False,
        }
    return result


def main():
    verify()
    OUTPUT.mkdir(exist_ok=True)
    for run_id in IDS:
        directory = ROOT / "runs" / run_id
        for name, sha in json.loads((directory / "completion.json").read_text()).items():
            assert digest(directory / name) == sha, (run_id, name)
    summaries, annual, counts, groups, exits, stats = [], [], [], [], [], []
    for asset in ("BTC", "ETH"):
        market = prepare_market(pd.read_parquet(ROOT / "runs" / f"{asset}_MRL" / "market.parquet"))
        for kind in ("MRL", "TFS"):
            a, b, c = event_study(asset, kind, market)
            summaries.extend(a)
            annual.extend(b)
            counts.append(c)
            a, b, c = trade_attribution(asset, kind, market)
            groups.extend(a)
            exits.extend(b)
            stats.append(c)
            print("ANALYZED", asset, kind, flush=True)
    summary = pd.DataFrame(summaries)
    summary.to_csv(OUTPUT / "signal_summary.csv", index=False)
    pd.DataFrame(annual).to_csv(OUTPUT / "signal_annual.csv", index=False)
    pd.DataFrame(groups).to_csv(OUTPUT / "trade_groups.csv", index=False)
    pd.DataFrame(exits).to_csv(OUTPUT / "exit_size_comparison.csv", index=False)
    json_write(OUTPUT / "signal_counts.json", counts)
    json_write(OUTPUT / "trade_summary.json", stats)
    json_write(ROOT / "decision.json", decision(summary))
    verify()
    json_write(
        OUTPUT / "completion.json",
        {p.name: digest(p) for p in sorted(OUTPUT.iterdir()) if p.name != "completion.json"},
    )
    print("COMPLETE", flush=True)


if __name__ == "__main__":
    main()
