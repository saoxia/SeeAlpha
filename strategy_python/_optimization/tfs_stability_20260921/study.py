"""Predeclared native-engine stability study; no parameter selection or downloads."""

from __future__ import annotations

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
from nautilus_trader.model.currencies import USDT
from nautilus_trader.model.data import BarType
from nautilus_trader.model.enums import AccountType, OmsType
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.objects import Money
from nautilus_trader.persistence.catalog import ParquetDataCatalog

from engine.pipeline.config import load_settings
from strategy_python._backtesting import BacktestSpec, load_binance_data

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parents[2]
CANDIDATE = "btc_cycle_optimized_20260915_tfs_original"
CONTROL = "_optimization.tfs_stability_20260921.control"
STOP_ATR = 1.460527352073367
STOP_SCALES = (0.8, 0.9, 1.0, 1.1, 1.2)
LOCKS = (0.70, 0.75, 0.80, 0.85, 0.90)


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def experiments():
    rows = []
    for asset in ("BTC", "ETH"):
        for scale in STOP_SCALES:
            for lock in LOCKS:
                for fee in (0, 5):
                    rows.append(
                        {
                            "id": f"{asset}_s{round(scale * 100)}_l{round(lock * 100)}_fee{fee}",
                            "asset": asset,
                            "kind": "grid",
                            "stop_scale": scale,
                            "lock": lock,
                            "extra_fee_bp": fee,
                            "starting_balance": 500,
                        }
                    )
        rows.extend(
            [
                {
                    "id": f"{asset}_center_fee2",
                    "asset": asset,
                    "kind": "cost_center",
                    "stop_scale": 1.0,
                    "lock": 0.8,
                    "extra_fee_bp": 2,
                    "starting_balance": 500,
                },
                {
                    "id": f"{asset}_center_balance5000",
                    "asset": asset,
                    "kind": "size_center",
                    "stop_scale": 1.0,
                    "lock": 0.8,
                    "extra_fee_bp": 0,
                    "starting_balance": 5000,
                },
                {
                    "id": f"{asset}_legacy_control",
                    "asset": asset,
                    "kind": "control",
                    "stop_scale": None,
                    "lock": None,
                    "extra_fee_bp": 0,
                    "starting_balance": 500,
                },
            ]
        )
    return rows


def source_files():
    dirs = [
        PROJECT / "strategy_python" / x.replace(".", "/")
        for x in (
            CANDIDATE,
            CONTROL,
            "_indicator",
            "_mechanism",
            "_risk",
            "_model",
            "_execution",
        )
    ]
    files = {p for d in dirs for p in d.rglob("*.py")}
    files.update((PROJECT / "pipeline").glob("*.py"))
    files.update(
        [PROJECT / "strategy_python/_backtesting.py", Path(__file__), ROOT / "PROTOCOL.md"]
    )
    return sorted(files)


def freeze():
    if (ROOT / "manifest.json").exists():
        raise RuntimeError("Manifest already exists; verify it instead of rewriting the experiment")
    catalog = Path(load_settings().catalog_dir)
    data = [
        p
        for p in catalog.rglob("*.parquet")
        if any(
            token in str(p)
            for asset in ("BTC", "ETH")
            for token in (
                f"{asset}USDT-PERP.BINANCE-1-MINUTE-LAST-EXTERNAL",
                f"crypto_perpetual/{asset}USDT",
            )
        )
    ]
    # Instruments may be stored under a typed instrument directory.
    data += [
        p
        for p in catalog.rglob("*.parquet")
        if "crypto_perpetual" in str(p) and any(f"{a}USDT" in str(p) for a in ("BTC", "ETH"))
    ]
    if not data:
        raise RuntimeError("No matching catalog files")
    manifest = {
        "created_at": datetime.now(UTC).isoformat(),
        "start": "2021-01-01",
        "end_exclusive": "2026-08-31",
        "warmup_days": 21,
        "candidate": CANDIDATE,
        "control": CONTROL,
        "experiments": experiments(),
        "source_sha256": {str(p.relative_to(PROJECT)): digest(p) for p in source_files()},
        "data_sha256": {str(p.relative_to(PROJECT)): digest(p) for p in sorted(set(data))},
    }
    (ROOT / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    print(
        "FROZEN",
        len(manifest["experiments"]),
        "experiments",
        len(manifest["data_sha256"]),
        "data files",
    )


def verify(manifest, *, data=False):
    items = dict(manifest["source_sha256"])
    if data:
        items.update(manifest["data_sha256"])
    for name, expected in items.items():
        if digest(PROJECT / name) != expected:
            raise RuntimeError(f"Frozen input changed: {name}")


def create_strategy(row, start):
    control = row["kind"] == "control"
    module = importlib.import_module("strategy_python." + (CONTROL if control else CANDIDATE))
    prefix = "TrendFollowingShortOptimized5Min" if control else "MechanismTrendFollowingShort5Min"
    asset = row["asset"]
    overrides = (
        {}
        if control
        else {
            "mechanism_stop_atr": STOP_ATR * row["stop_scale"],
            "peak_profit_lock": row["lock"],
        }
    )
    config = getattr(module, prefix + "Config")(
        instrument_id=InstrumentId.from_str(f"{asset}USDT-PERP.BINANCE"),
        bar_type=BarType.from_str(f"{asset}USDT-PERP.BINANCE-1-MINUTE-LAST-EXTERNAL"),
        trade_start_ns=int(start.timestamp() * 1e9),
        **overrides,
    )
    return getattr(module, prefix + "Strategy")(config)


def fee_instrument(instrument, extra_bp):
    fields = instrument.to_dict(instrument)
    extra = Decimal(str(extra_bp)) / 10000
    for name in ("maker_fee", "taker_fee"):
        fields[name] = str(Decimal(fields[name]) + extra)
    return type(instrument).from_dict(fields)


def measure(engine, marks, start, end, output, initial):
    fills = engine.trader.generate_order_fills_report()
    positions = engine.trader.generate_positions_report()
    account = engine.trader.generate_account_report(BINANCE_VENUE)
    for name, frame in (("fills", fills), ("positions", positions), ("account", account)):
        frame.to_csv(output / f"{name}.csv")
    marks = marks[(marks.index >= start) & (marks.index < end)]
    if marks.empty or marks.index.has_duplicates or not marks.index.is_monotonic_increasing:
        raise RuntimeError("Invalid market timestamp coverage")
    events = pd.DataFrame(0.0, index=marks.index, columns=["cash", "qty", "turnover"])
    trades = pd.DataFrame(columns=["pnl", "entry_time", "exit_time", "notional", "fees"])
    if not fills.empty:
        if not (fills.status.astype(str) == "FILLED").all():
            raise RuntimeError("Partial fills require incremental accounting")
        fees = fills.commissions.map(
            lambda s: sum(
                float(v.split()[0]) for v in (ast.literal_eval(s) if isinstance(s, str) else s)
            )
        )
        quantities = fills.filled_qty.astype(float)
        signed = np.where(fills.side == "BUY", 1, -1) * quantities
        notional = quantities * fills.avg_px.astype(float)
        flows = -signed * fills.avg_px.astype(float) - fees
        time_index = pd.to_datetime(fills.ts_last, utc=True)
        raw = pd.DataFrame(
            {"cash": flows.to_numpy(), "qty": signed.to_numpy(), "turnover": notional.to_numpy()},
            index=time_index,
        )
        if not raw.index.isin(marks.index).all():
            raise RuntimeError("Fills outside observed market marks")
        events = raw.groupby(level=0).sum().reindex(marks.index, fill_value=0.0)
        grouped = pd.DataFrame(
            {
                "id": fills.position_id,
                "pnl": flows,
                "time": time_index,
                "notional": notional,
                "fees": fees,
                "qty": signed,
            }
        )
        trades = grouped.groupby("id").agg(
            pnl=("pnl", "sum"),
            entry_time=("time", "min"),
            exit_time=("time", "max"),
            notional=("notional", "sum"),
            fees=("fees", "sum"),
            residual_qty=("qty", "sum"),
        )
        if (trades.residual_qty.abs() > 1e-7).any():
            raise RuntimeError("Unclosed position cash flow")
    cumulative = events.cumsum()
    equity = initial + cumulative.cash + cumulative.qty * marks
    if abs(cumulative.qty.iloc[-1]) > 1e-7:
        raise RuntimeError("Unclosed final exposure")
    balance = engine.cache.account_for_venue(BINANCE_VENUE).balance_total(USDT).as_double()
    pnl = float(balance - initial)
    if abs(equity.iloc[-1] - balance) > 1e-4 or abs(trades.pnl.sum() - pnl) > 1e-4:
        raise RuntimeError("Account / marked equity / trade cash flow mismatch")
    dd = 1 - equity / equity.cummax().clip(lower=initial)
    wins, losses = trades.pnl[trades.pnl > 0], trades.pnl[trades.pnl < 0]
    annual = equity.resample("YE").last()
    change = annual.diff()
    change.iloc[0] = annual.iloc[0] - initial
    review_start = pd.Timestamp("2025-01-01", tz="UTC")
    prior = equity[equity.index < review_start]
    later = equity[equity.index >= review_start]
    review_pnl = float(later.iloc[-1] - prior.iloc[-1]) if len(prior) and len(later) else None
    result = {
        "net_pnl": pnl,
        "return_pct": pnl / initial * 100,
        "max_drawdown_pct": float(dd.max() * 100),
        "trades": len(trades),
        "fees": float(trades.fees.sum()),
        "turnover": float(trades.notional.sum()),
        "profit_factor": float(wins.sum() / -losses.sum()) if len(losses) else None,
        "payoff_ratio": float(wins.mean() / -losses.mean()) if len(wins) and len(losses) else None,
        "win_rate_pct": float((trades.pnl > 0).mean() * 100) if len(trades) else None,
        "top5_profit": float(wins.nlargest(5).sum()),
        "pnl_without_best5": float(pnl - wins.nlargest(5).sum()),
        "worst5_pnl": float(losses.nsmallest(5).sum()),
        "annual_marked_pnl": {str(k.year): float(v) for k, v in change.items()},
        "review_marked_pnl": review_pnl,
        "review_closed_trades": int((trades.exit_time >= review_start).sum()) if len(trades) else 0,
        "expected_minutes": int((end - start).total_seconds() / 60),
        "observed_minutes": len(marks),
        "missing_minutes": int((end - start).total_seconds() / 60) - len(marks),
        "first_bar": str(marks.index[0]),
        "last_bar": str(marks.index[-1]),
        "cashflow_error": float(trades.pnl.sum() - pnl),
    }
    trades.to_csv(output / "trades.csv")
    pd.DataFrame({"equity": equity, "drawdown_pct": dd * 100}).resample("1D").last().to_csv(
        output / "daily_equity.csv"
    )
    annual_rows = []
    for year, block in equity.groupby(equity.index.year):
        before = equity[equity.index < block.index[0]]
        base = float(before.iloc[-1]) if len(before) else initial
        annual_rows.append(
            {
                "year": year,
                "net_pnl": float(block.iloc[-1] - base),
                "return_pct": float((block.iloc[-1] / base - 1) * 100),
                "max_drawdown_pct": float(
                    (1 - block / block.cummax().clip(lower=base)).max() * 100
                ),
                "closed_trades": int((trades.exit_time.dt.year == year).sum())
                if len(trades)
                else 0,
            }
        )
    pd.DataFrame(annual_rows).to_csv(output / "annual.csv", index=False)
    return result


def run(experiment_id, *, check=False, batch_days=90):
    manifest = json.loads((ROOT / "manifest.json").read_text())
    verify(manifest)
    row = next(r for r in manifest["experiments"] if r["id"] == experiment_id)
    start = datetime.fromisoformat("2022-05-01" if check else manifest["start"]).replace(tzinfo=UTC)
    end = datetime.fromisoformat("2022-06-01" if check else manifest["end_exclusive"]).replace(
        tzinfo=UTC
    )
    output = ROOT / ("checks" if check else "runs") / experiment_id
    output.mkdir(parents=True, exist_ok=True)
    strategy = create_strategy(row, start)
    spec = BacktestSpec(
        instrument_id=strategy.config.instrument_id,
        data_symbol=row["asset"] + "USDT",
        bar_type=strategy.config.bar_type,
        interval="1m",
        warmup_days=21,
        oms_type=OmsType.HEDGING,
        starting_balance=Decimal(row["starting_balance"]),
        leverage=Decimal(5),
        prepare_data_before_run=False,
    )
    catalog = ParquetDataCatalog(str(load_settings().catalog_dir))
    original = max(
        catalog.instruments(instrument_ids=[str(spec.instrument_id)]), key=lambda x: x.ts_init
    )
    instrument = fee_instrument(original, row["extra_fee_bp"])
    params = {
        "experiment": row,
        "start": start.isoformat(),
        "end_exclusive": end.isoformat(),
        "config": json.loads(strategy.config.json()),
        "instrument": instrument.to_dict(instrument),
        "manifest_sha256": digest(ROOT / "manifest.json"),
        "batch_days": batch_days,
        "nautilus_version": importlib.import_module("nautilus_trader").__version__,
    }
    if (output / "metrics.json").exists():
        if json.loads((output / "parameters.json").read_text()) != params:
            raise RuntimeError(f"Stale completed result: {output}")
        print("CACHED", experiment_id, flush=True)
        return output
    (output / "parameters.json").write_text(json.dumps(params, indent=2) + "\n")
    engine = BacktestEngine(config=BacktestEngineConfig(logging=LoggingConfig(bypass_logging=True)))
    began = time.monotonic()
    marks = []
    try:
        engine.add_venue(
            venue=BINANCE_VENUE,
            oms_type=OmsType.HEDGING,
            account_type=AccountType.MARGIN,
            base_currency=None,
            starting_balances=[Money(row["starting_balance"], instrument.quote_currency)],
            default_leverage=Decimal(5),
        )
        engine.add_instrument(instrument)
        engine.add_strategy(strategy)
        cursor = start - timedelta(days=21)
        while cursor < end:
            stop = min(cursor + timedelta(days=batch_days), end)
            _, bars = load_binance_data(cursor, stop, spec, check_data=False)
            if bars:
                marks.append(
                    pd.Series(
                        [float(b.close) for b in bars],
                        index=pd.to_datetime([b.ts_event for b in bars], utc=True),
                    )
                )
                engine.add_data(bars)
                engine.run(streaming=True)
                engine.clear_data()
            print("BATCH", experiment_id, stop.isoformat(), flush=True)
            cursor = stop
        engine.end()
        result = measure(engine, pd.concat(marks), start, end, output, row["starting_balance"])
        reasons = Counter()
        statuses = Counter()
        off_tick = 0
        for order in engine.cache.orders():
            statuses[str(order.status)] += 1
            for attr in ("price", "trigger_price"):
                value = getattr(order, attr, None)
                if (
                    value is not None
                    and value.as_decimal() % instrument.price_increment.as_decimal() != 0
                ):
                    off_tick += 1
            for event in order.events:
                reason = getattr(event, "reason", None)
                if reason:
                    reasons[type(event).__name__ + ": " + str(reason)] += 1
        if off_tick:
            raise RuntimeError("Off-tick order price")
        result.update(
            order_status_counts=dict(statuses),
            order_event_reasons=dict(reasons),
            off_tick_order_price_fields=off_tick,
            elapsed_seconds=round(time.monotonic() - began, 2),
        )
        (output / "metrics.json").write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")
        print("DONE", experiment_id, result["net_pnl"], flush=True)
        return output
    finally:
        engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["freeze", "verify", "run"])
    parser.add_argument("experiment_id", nargs="?")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if args.action == "freeze":
        freeze()
    elif args.action == "verify":
        verify(json.loads((ROOT / "manifest.json").read_text()), data=True)
    else:
        run(args.experiment_id, check=args.check)
