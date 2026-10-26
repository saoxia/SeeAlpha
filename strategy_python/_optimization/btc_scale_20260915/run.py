"""BTC scale evaluation; isolated outputs, native engine, continuous streaming."""

import argparse
import ast
import hashlib
import importlib
import json
import time
from collections import Counter
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import numpy as np
import pandas as pd
from nautilus_trader.adapters.binance import BINANCE_VENUE
from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.config import BacktestEngineConfig, LoggingConfig
from nautilus_trader.model.data import BarType
from nautilus_trader.model.enums import AccountType, OmsType
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.objects import Money
from nautilus_trader.persistence.catalog import ParquetDataCatalog

from pipeline.config import load_settings
from strategy_python._backtesting import BacktestSpec, load_binance_data
from strategy_python._optimization.four_strategy_20260914.research import measure

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parents[2]
STRATEGIES = json.loads((ROOT / "strategies.json").read_text())
PERIODS = {
    "full": ("2021-01-01", "2026-08-31"),
    "recent": ("2025-01-01", "2026-08-31"),
    "pilot": ("2022-05-01", "2022-06-01"),
}


def spec_for(key):
    return BacktestSpec(
        instrument_id=InstrumentId.from_str("BTCUSDT-PERP.BINANCE"),
        data_symbol="BTCUSDT",
        bar_type=BarType.from_str("BTCUSDT-PERP.BINANCE-1-MINUTE-LAST-EXTERNAL"),
        interval="1m",
        warmup_days=STRATEGIES[key]["warmup_days"],
        oms_type=OmsType.HEDGING,
        starting_balance=Decimal(500),
        leverage=Decimal(5),
        prepare_data_before_run=False,
    )


def create_strategy(key, start, overrides=None):
    info = STRATEGIES[key]
    module = importlib.import_module("strategy_python." + info["package"])
    spec = spec_for(key)
    config = getattr(module, info["prefix"] + "Config")(
        instrument_id=spec.instrument_id,
        bar_type=spec.bar_type,
        trade_start_ns=int(start.timestamp() * 1_000_000_000),
        **(overrides or {}),
    )
    return getattr(module, info["prefix"] + "Strategy")(config)


def trade_metrics(output, balance_pnl):
    fills = pd.read_csv(output / "fills.csv")
    if fills.empty:
        return {"payoff_ratio": None, "turnover_usdt": 0, "cashflow_trades": 0}
    fees = fills.commissions.map(
        lambda value: sum(float(x.split()[0]) for x in ast.literal_eval(value))
    )
    notional = fills.filled_qty.astype(float) * fills.avg_px.astype(float)
    flows = np.where(fills.side == "SELL", 1, -1) * notional - fees
    pnl = pd.Series(flows, index=fills.index).groupby(fills.position_id).sum()
    assert abs(pnl.sum() - balance_pnl) < 0.0001, (pnl.sum(), balance_pnl)
    winners, losers = pnl[pnl > 0], pnl[pnl < 0]
    return {
        "cashflow_trades": len(pnl),
        "cashflow_win_rate_pct": float((pnl > 0).mean() * 100),
        "payoff_ratio": float(winners.mean() / -losers.mean())
        if len(winners) and len(losers)
        else None,
        "cashflow_profit_factor": float(winners.sum() / -losers.sum()) if len(losers) else None,
        "turnover_usdt": float(notional.sum()),
        "cashflow_reconciliation_error": float(pnl.sum() - balance_pnl),
    }


def run(key, period, *, matched=False, batch_days=90, mode="stream"):
    if matched and key != "PBS_original":
        raise ValueError("Matched budget applies only to original PBS")
    output = ROOT / "runs" / period / (key + ("_matched" if matched else ""))
    if period == "pilot":
        output = output / mode
    output.mkdir(parents=True, exist_ok=True)
    start, end = [datetime.fromisoformat(s).replace(tzinfo=UTC) for s in PERIODS[period]]
    spec = spec_for(key)
    overrides = {"risk_fraction": 0.015, "max_notional_fraction": 3.0} if matched else {}
    strategy = create_strategy(key, start, overrides)
    hashes = {
        str(p.relative_to(PROJECT)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in (PROJECT / "strategy_python" / STRATEGIES[key]["package"]).glob("*.py")
    }
    params = {
        "key": key,
        "period": period,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "matched_budget": matched,
        "overrides": overrides,
        "config": json.loads(strategy.config.json()),
        "source_sha256": hashes,
        "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "batch_days": batch_days,
        "mode": mode,
    }
    if (output / "metrics.json").exists():
        old = json.loads((output / "parameters.json").read_text())
        if any(old.get(k) != v for k, v in params.items()):
            raise RuntimeError(f"Stale run requires separate output: {output}")
        print("CACHED", key, period, matched, flush=True)
        return output
    instrument = max(
        ParquetDataCatalog(str(load_settings().catalog_dir)).instruments(
            instrument_ids=[str(spec.instrument_id)]
        ),
        key=lambda x: x.ts_init,
    )
    params.update(
        {
            "maker_fee": str(instrument.maker_fee),
            "taker_fee": str(instrument.taker_fee),
            "price_increment": str(instrument.price_increment),
            "size_increment": str(instrument.size_increment),
            "min_notional": str(instrument.min_notional),
            "starting_balance": 500,
            "account_leverage": 5,
            "warmup_days": spec.warmup_days,
            "nautilus_version": importlib.import_module("nautilus_trader").__version__,
        }
    )
    (output / "parameters.json").write_text(json.dumps(params, indent=2) + "\n")
    engine = BacktestEngine(config=BacktestEngineConfig(logging=LoggingConfig(bypass_logging=True)))
    marks = []
    started = time.monotonic()
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
        engine.add_strategy(strategy)
        cursor = start - timedelta(days=spec.warmup_days)
        while cursor < end:
            batch_end = end if mode == "normal" else min(cursor + timedelta(days=batch_days), end)
            _, bars = load_binance_data(cursor, batch_end, spec, check_data=False)
            if bars:
                marks.append(
                    pd.Series(
                        [float(b.close) for b in bars],
                        index=pd.to_datetime([b.ts_event for b in bars], utc=True),
                    )
                )
                engine.add_data(bars)
                engine.run(streaming=(mode == "stream"))
                if mode == "stream":
                    engine.clear_data()
            print("BATCH", key, period, matched, batch_end.isoformat(), flush=True)
            cursor = batch_end
        if mode == "stream":
            engine.end()
        result = measure(engine, pd.concat(marks), start, end, output)
        result.update(trade_metrics(output, result["net_pnl"]))
        mark = pd.concat(marks)
        mark = mark[(mark.index >= start) & (mark.index < end)]
        expected = int((end - start).total_seconds() / 60)
        assert not mark.index.has_duplicates
        result["expected_minutes"] = expected
        result["observed_minutes"] = len(mark)
        result["missing_minutes_including_edges"] = expected - len(mark)
        statuses = Counter()
        reasons = Counter()
        off_tick = 0
        tick = instrument.price_increment.as_decimal()
        for order in engine.cache.orders():
            statuses[str(order.status)] += 1
            for attr in ("price", "trigger_price"):
                price = getattr(order, attr, None)
                if price is not None and price.as_decimal() % tick != 0:
                    off_tick += 1
            for event in order.events:
                reason = getattr(event, "reason", None)
                if reason:
                    reasons[type(event).__name__ + ": " + str(reason)] += 1
        result["order_status_counts"] = dict(statuses)
        result["order_event_reasons"] = dict(reasons)
        result["off_tick_order_price_fields"] = off_tick
        result["elapsed_seconds"] = round(time.monotonic() - started, 2)
        assert off_tick == 0, f"BTC off-tick orders: {off_tick}"
        (output / "metrics.json").write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")
        print("RESULT", key, period, matched, result["net_pnl"], flush=True)
        return output
    finally:
        engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("key", choices=STRATEGIES)
    parser.add_argument("period", choices=PERIODS)
    parser.add_argument("--matched", action="store_true")
    parser.add_argument("--batch-days", type=int, default=90)
    parser.add_argument("--mode", choices=("stream", "normal"), default="stream")
    args = parser.parse_args()
    run(args.key, args.period, matched=args.matched, batch_days=args.batch_days, mode=args.mode)
