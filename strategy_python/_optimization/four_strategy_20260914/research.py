"""Deterministic native-engine trials; original strategy packages are read-only inputs."""

from __future__ import annotations

import argparse
import ast
import fcntl
import importlib
import json
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import numpy as np
import pandas as pd
from nautilus_trader.adapters.binance import BINANCE_VENUE
from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.config import BacktestEngineConfig, LoggingConfig
from nautilus_trader.model.currencies import USDT
from nautilus_trader.model.data import Bar
from nautilus_trader.model.enums import AccountType, OmsType
from nautilus_trader.model.objects import Money, Price, Quantity
from nautilus_trader.persistence.catalog import ParquetDataCatalog

from engine.pipeline.config import load_settings
from strategy_python._backtesting import load_binance_data, with_input_interval
from strategy_python._optimization.four_strategy_20260914.environment import backtest_spec
from strategy_python.portfolio import STRATEGY_REGISTRY

ROOT = Path(__file__).resolve().parent
NAMES = {
    "MRL": "mean_reversion_long_5min",
    "TFL": "trend_following_long_5min",
    "TFS": "trend_following_short_5min",
    "PBS": "pullback_short_1h",
}
BASE = {
    "MRL": {},
    "TFL": {},
    "TFS": {},
    "PBS": {"risk_fraction": 0.015, "max_notional_fraction": 3.0},
}
PERIODS = {
    "parity": ("2022-05-01", "2022-06-01"),
    "pilot": ("2023-01-01", "2023-02-01"),
    "train": ("2021-01-01", "2024-01-01"),
    "validation": ("2024-01-01", "2025-01-01"),
    "holdout": ("2025-01-01", "2026-09-01"),
    "full": ("2021-01-01", "2026-09-01"),
}
CANDIDATES = {
    "MRL": {
        "baseline": {},
        "faster_reversion": {"take_profit_rate": 0.003},
        "wider_reversion": {"take_profit_rate": 0.0075},
        "full_reversion": {"take_profit_rate": 0.01},
        "expire_24h": {"max_holding_hours": 24},
        "allow_72h": {"max_holding_hours": 72},
        "stronger_trend": {"ma_reentry_ratio": 0.45},
        "moderate_trend": {"ma_reentry_ratio": 0.55},
        "deeper_discount": {"bollinger_std_multiplier": 2.5},
        "moderate_discount": {"bollinger_std_multiplier": 2.25},
    },
    "PBS": {
        "baseline": {},
        "rr2": {"reward_risk": 2.0},
        "rr3": {"reward_risk": 3.0},
        "rr5": {"reward_risk": 5.0},
        "rr6": {"reward_risk": 6.0},
        "expire_12h": {"max_holding_hours": 12},
        "allow_36h": {"max_holding_hours": 36},
        "allow_48h": {"max_holding_hours": 48},
        "local_structure": {"swing_period": 6},
        "broad_structure": {"swing_period": 24},
    },
}
for key in ("TFL", "TFS"):
    CANDIDATES[key] = {
        "baseline": {},
        "protect_1pct": {"take_profit_activation_return": 0.01},
        "protect_1_5pct": {"take_profit_activation_return": 0.015},
        "extend_3pct": {"take_profit_activation_return": 0.03},
        "extend_4pct": {"take_profit_activation_return": 0.04},
        "loose_trail": {"peak_profit_lock": 0.5},
        "tight_trail": {"peak_profit_lock": 0.8},
        "stop_cooldown": {"stop_loss_cooldown_seconds": 3600},
        "faster_cross": {"cross_fast_period": 26},
        "slower_cross": {"cross_fast_period": 65},
    }


# Second-round hypotheses, specified from development screens before holdout.
for key in ("TFL", "TFS"):
    CANDIDATES[key].update(
        {
            "extend3_tight": {"take_profit_activation_return": 0.03, "peak_profit_lock": 0.8},
            "extend4_tight": {"take_profit_activation_return": 0.04, "peak_profit_lock": 0.8},
        }
    )
CANDIDATES["MRL"].update(
    {
        "expire24_wider": {"max_holding_hours": 24, "take_profit_rate": 0.0075},
        "expire24_deeper": {"max_holding_hours": 24, "bollinger_std_multiplier": 2.25},
    }
)
CANDIDATES["PBS"].update(
    {
        "broad_rr3": {"swing_period": 24, "reward_risk": 3.0},
        "broad_rr5": {"swing_period": 24, "reward_risk": 5.0},
    }
)


def measure(engine, bars, start, end, output, interval_minutes=1):
    fills = engine.trader.generate_order_fills_report()
    positions = engine.trader.generate_positions_report()
    account = engine.trader.generate_account_report(BINANCE_VENUE)
    for name, frame in [("fills", fills), ("positions", positions), ("account", account)]:
        frame.to_csv(output / f"{name}.csv")
    marks = (
        bars
        if isinstance(bars, pd.Series)
        else pd.Series(
            [float(b.close) for b in bars],
            index=pd.to_datetime([b.ts_event for b in bars], utc=True),
        )
    )
    marks = marks[(marks.index >= start) & (marks.index < end)]
    if marks.empty:
        raise RuntimeError("No trading-period marks")
    if marks.index.has_duplicates:
        raise RuntimeError("Duplicate market timestamps")
    cash = pd.Series(500.0, index=marks.index)
    qty = pd.Series(0.0, index=marks.index)
    turnover = pd.Series(0.0, index=marks.index)
    fees = 0.0
    if not fills.empty:
        if not (fills.status.astype(str) == "FILLED").all():
            raise RuntimeError("Partial fills require incremental fill audit")
        fee = fills.commissions.map(
            lambda v: sum(
                float(x.split()[0]) for x in (ast.literal_eval(v) if isinstance(v, str) else v)
            )
        )
        signed = np.where(fills.side == "BUY", 1.0, -1.0) * fills.filled_qty.astype(float)
        notional = fills.filled_qty.astype(float) * fills.avg_px.astype(float)
        events = pd.DataFrame(
            {
                "cash": -signed * fills.avg_px.astype(float) - fee,
                "qty": signed,
                "turnover": notional,
            }
        )
        events.index = pd.to_datetime(fills.ts_last, utc=True)
        events = events.groupby(level=0).sum()
        if not events.index.isin(marks.index).all():
            raise RuntimeError("Fills not aligned with marks")
        events = events.reindex(marks.index, fill_value=0.0)
        cash += events.cash.cumsum()
        qty += events.qty.cumsum()
        turnover += events.turnover.cumsum()
        fees = float(fee.sum())
    equity = cash + qty * marks
    if abs(qty.iloc[-1]) > 1e-7:
        raise RuntimeError("Unclosed final exposure")
    balance = float(engine.cache.account_for_venue(BINANCE_VENUE).balance_total(USDT).as_double())
    if abs(equity.iloc[-1] - balance) > 0.0001:
        raise RuntimeError(f"Account reconciliation: {equity.iloc[-1]} != {balance}")
    dd = 1 - equity / equity.cummax().clip(lower=500)
    pnls = (
        positions.realized_pnl.str.split().str[0].astype(float)
        if not positions.empty
        else pd.Series(dtype=float)
    )
    stress = {}
    for bps in (2, 5, 10):
        stressed = equity - turnover * bps / 10000
        stress[str(bps)] = {
            "net_pnl": float(stressed.iloc[-1] - 500),
            "drawdown_pct": float((1 - stressed / stressed.cummax().clip(lower=500)).max() * 100),
        }
    gaps = marks.index.to_series().diff()
    missing = gaps[gaps > pd.Timedelta(minutes=interval_minutes)]
    result = {
        "net_pnl": balance - 500,
        "return_pct": (balance / 500 - 1) * 100,
        "max_drawdown_pct": float(dd.max() * 100),
        "trades": len(positions),
        "fees": fees,
        "win_rate_pct": float((pnls > 0).mean() * 100) if len(pnls) else 0.0,
        "profit_factor": float(pnls[pnls > 0].sum() / -pnls[pnls < 0].sum())
        if (pnls < 0).any()
        else None,
        "stress": stress,
        "missing_minutes": int(
            ((missing / pd.Timedelta(minutes=interval_minutes) - 1) * interval_minutes).sum()
        ),
        "first_bar": str(marks.index[0]),
        "last_bar": str(marks.index[-1]),
    }
    yearly = equity.resample("YE").last()
    changes = yearly.diff()
    changes.iloc[0] = yearly.iloc[0] - 500
    result["annual_marked_pnl"] = {str(k.year): float(v) for k, v in changes.items()}
    pd.DataFrame({"equity": equity, "drawdown_pct": dd * 100}).resample("1D").last().to_csv(
        output / "daily_equity.csv"
    )
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("strategy", choices=NAMES)
    parser.add_argument("period", choices=PERIODS)
    parser.add_argument("--candidates", default="all")
    parser.add_argument("--interval", default="1m", choices=["1m", "5m"])
    parser.add_argument("--optimized", action="store_true")
    parser.add_argument("--round2", action="store_true")
    args = parser.parse_args()
    key = args.strategy
    start, end = [datetime.fromisoformat(t).replace(tzinfo=UTC) for t in PERIODS[args.period]]
    spec = backtest_spec(key)
    spec = with_input_interval(spec, args.interval)
    config_class, strategy_class = STRATEGY_REGISTRY[NAMES[key]]
    if args.optimized:
        from strategy_python._optimization.four_strategy_20260914.materialize import (
            CLASSES,
            PACKAGES,
        )

        module = importlib.import_module("strategy_python." + PACKAGES[key])
        config_class = getattr(module, CLASSES[key][1] + "Config")
        strategy_class = getattr(module, CLASSES[key][1] + "Strategy")
    chosen = (
        CANDIDATES[key]
        if args.candidates == "all"
        else {c: CANDIDATES[key][c] for c in args.candidates.split(",")}
    )
    t = time.monotonic()
    data_start = start - timedelta(days=spec.warmup_days)
    if args.interval == "1m":
        instrument, bars = load_binance_data(data_start, end, spec, check_data=False)
    else:
        instrument = max(
            ParquetDataCatalog(str(load_settings().catalog_dir)).instruments(
                instrument_ids=[str(spec.instrument_id)]
            ),
            key=lambda i: i.ts_init,
        )
        frame = pd.read_parquet(ROOT / "derived_5m.parquet")
        frame = frame[
            (frame.ts >= int(data_start.timestamp() * 1e9))
            & (frame.ts < int(end.timestamp() * 1e9))
        ]
        bars = [
            Bar(
                spec.bar_type,
                Price(o, instrument.price_precision),
                Price(h, instrument.price_precision),
                Price(l, instrument.price_precision),
                Price(c, instrument.price_precision),
                Quantity(v, instrument.size_precision),
                int(ts),
                int(ts),
            )
            for ts, o, h, l, c, v in frame.itertuples(index=False, name=None)
        ]
    if instrument is None or not bars:
        raise RuntimeError("No data for experiment")
    print("LOADED", key, args.period, len(bars), round(time.monotonic() - t, 2), flush=True)
    for name, params in chosen.items():
        output = (
            ROOT
            / (
                "frozen_runs"
                if args.optimized
                else "runs"
                if args.interval == "1m"
                else "screen_5m_round2"
                if args.round2
                else "screen_5m"
            )
            / key
            / args.period
            / name
        )
        output.mkdir(parents=True, exist_ok=True)
        lockfile = (output / ".lock").open("w")
        fcntl.flock(lockfile, fcntl.LOCK_EX)
        if (output / "metrics.json").exists():
            print("CACHED", key, args.period, name, flush=True)
            lockfile.close()
            continue
        output.mkdir(parents=True, exist_ok=True)
        overrides = {} if args.optimized else {**BASE[key], **params}
        strategy = strategy_class(
            config_class(
                instrument_id=spec.instrument_id,
                bar_type=spec.bar_type,
                trade_start_ns=int(start.timestamp() * 1e9),
                **overrides,
            )
        )
        (output / "parameters.json").write_text(
            json.dumps(
                {
                    "strategy": NAMES[key],
                    "candidate": name,
                    "strategy_class": type(strategy).__module__ + "." + type(strategy).__name__,
                    "config": json.loads(strategy.config.json()),
                    "start": start.isoformat(),
                    "end": end.isoformat(),
                    "maker_fee": str(instrument.maker_fee),
                    "taker_fee": str(instrument.taker_fee),
                    "nautilus_version": importlib.import_module("nautilus_trader").__version__,
                },
                indent=2,
            )
            + "\n"
        )
        engine = BacktestEngine(
            config=BacktestEngineConfig(logging=LoggingConfig(bypass_logging=True))
        )
        t = time.monotonic()
        try:
            engine.add_venue(
                venue=BINANCE_VENUE,
                oms_type=OmsType.HEDGING,
                account_type=AccountType.MARGIN,
                base_currency=None,
                starting_balances=[Money(500, instrument.quote_currency)],
                default_leverage=Decimal(5),
            )
            engine.add_instrument(instrument)
            engine.add_data(bars)
            engine.add_strategy(strategy)
            engine.run()
            print("ENGINE_SECONDS", round(time.monotonic() - t, 2), flush=True)
            result = measure(engine, bars, start, end, output, 1 if args.interval == "1m" else 5)
            result["elapsed_seconds"] = round(time.monotonic() - t, 2)
            (output / "metrics.json").write_text(
                json.dumps(result, indent=2, allow_nan=False) + "\n"
            )
            print("RESULT", key, args.period, name, json.dumps(result), flush=True)
        finally:
            engine.dispose()
            lockfile.close()


if __name__ == "__main__":
    main()
