"""Observational subclasses: delegate every trading action to frozen production code."""

import argparse
import json
import os
import subprocess
import sys
import time
import zipfile
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

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
from strategy_python._execution.entry_constraints import opening_order_allowed
from strategy_python._mechanism.rules import mean_reversion_snapshot, trend_snapshot
from strategy_python._optimization.btc_long_validation_20260921.run import (
    digest,
    environment,
    json_write,
)
from strategy_python._optimization.tfs_stability_20260921.study import measure
from strategy_python.btc_cycle_optimized_20260915_mrl_original import (
    MechanismMeanReversionLong5MinConfig as MRLConfig,
    MechanismMeanReversionLong5MinStrategy as MRLStrategy,
)
from strategy_python.btc_cycle_optimized_20260915_tfs_original import (
    MechanismTrendFollowingShort5MinConfig as TFSConfig,
    MechanismTrendFollowingShort5MinStrategy as TFSStrategy,
)

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parents[2]
START = datetime(2021, 1, 1, tzinfo=UTC)
END = datetime(2026, 8, 31, tzinfo=UTC)
IDS = [f"{asset}_{kind}" for asset in ("BTC", "ETH") for kind in ("MRL", "TFS")]
REFERENCES = {
    "BTC_MRL": ROOT.parent / "btc_long_validation_20260921/runs/mean_reversion_long_fee0",
    "BTC_TFS": ROOT.parent / "tfs_stability_20260921/runs/BTC_s100_l80_fee0",
    "ETH_TFS": ROOT.parent / "tfs_stability_20260921/runs/ETH_s100_l80_fee0",
}


class ObservedMRL(MRLStrategy):
    def __init__(self, config):
        super().__init__(config)
        self.observations = []
        self.capture = None

    def _on_five_minute_bar(self, bar):
        state = self.trade_state
        now = bar.ts_end - 1_000_000
        reason = (
            "invested"
            if state.invested
            else "cooldown"
            if bar.ts_end < state.cooldown_until_ns
            else "recovery_wait"
            if state.ma52_recovery_waiting
            else "risk_or_signal"
        )
        self.capture = {"status": reason, "quantity": 0.0}
        super()._on_five_minute_bar(bar)
        if now >= self.config.trade_start_ns and self.features.bollinger is not None:
            lower, middle, _ = self.features.bollinger
            ratio = self.features.adverse_ma99_ratio(state)
            snapshot = mean_reversion_snapshot(
                lower,
                middle,
                self.config.mechanism_add_multipliers,
                2 * self.config.entry_fee_rate,
            )
            valid = snapshot is not None
            eligible = valid and ratio is not None and ratio <= self.config.ma_reentry_ratio + 1e-12
            if eligible and self.capture["status"] == "risk_or_signal":
                self.capture["status"] = "risk_sizing_unavailable"
            self.observations.append(
                {
                    "ts": now,
                    "price": float(self.instrument.make_price(lower)),
                    "eligible": eligible,
                    "valid": valid,
                    "adverse_ratio": ratio,
                    **self.capture,
                }
            )
        self.capture = None

    def apply_entry_target(self, state, target):
        if self.capture is not None and target.role == "initial":
            allowed = opening_order_allowed(
                self.instrument,
                self.instrument.make_qty(target.quantity),
                self.instrument.make_price(target.price),
            )
            self.capture.update(
                status="admitted_quote" if allowed else "order_constraint",
                quantity=float(target.quantity),
            )
        super().apply_entry_target(state, target)


class ObservedTFS(TFSStrategy):
    def __init__(self, config):
        super().__init__(config)
        self.observations = []
        self.capture = None

    def _refresh_leg(self, state, levels, close):
        cfg = self.config
        allowed = self.hourly_trend.allows_short(
            require_price=cfg.require_hourly_price_filter,
            require_slope=cfg.require_hourly_slope_filter,
        )
        snapshot = trend_snapshot(
            close,
            self.mechanism_atr.value,
            cfg.mechanism_stop_atr,
            cfg.mechanism_activation_atr,
            cfg.mechanism_price_risk,
            cfg.equity_fraction_per_side,
        )
        candidate = (
            self._cross_signal == -1
            and allowed
            and self.daily_boll.allowed
            and snapshot is not None
        )
        self.capture = {"status": "invested" if state.invested else "state_gate", "quantity": 0.0}
        super()._refresh_leg(state, levels, close)
        if candidate:
            self.observations.append(
                {
                    "ts": self.clock.timestamp_ns(),
                    "price": close,
                    "eligible": True,
                    "valid": True,
                    **self.capture,
                }
            )
        self.capture = None

    def submit_entry_market(self, state, quantity, *, reference_price=None):
        if self.capture is not None:
            allowed = opening_order_allowed(self.instrument, quantity, reference_price)
            self.capture.update(
                status="admitted_market" if allowed else "order_constraint",
                quantity=float(quantity),
            )
        super().submit_entry_market(state, quantity, reference_price=reference_price)


def strategy_for(asset, kind):
    cls, config = (ObservedMRL, MRLConfig) if kind == "MRL" else (ObservedTFS, TFSConfig)
    return cls(
        config(
            instrument_id=InstrumentId.from_str(f"{asset}USDT-PERP.BINANCE"),
            bar_type=BarType.from_str(f"{asset}USDT-PERP.BINANCE-1-MINUTE-LAST-EXTERNAL"),
            trade_start_ns=int(START.timestamp() * 1e9),
        )
    )


def source_files():
    dirs = [
        "btc_cycle_optimized_20260915_mrl_original",
        "btc_cycle_optimized_20260915_tfs_original",
        "_indicator",
        "_mechanism",
        "_risk",
        "_model",
        "_execution",
    ]
    files = {p for d in dirs for p in (PROJECT / "strategy_python" / d).rglob("*.py")}
    files.update((PROJECT / "pipeline").glob("*.py"))
    files.update(
        [
            PROJECT / "strategy_python/_backtesting.py",
            PROJECT / "strategy_python/btc.py",
            ROOT.parent / "btc_long_validation_20260921/run.py",
            ROOT.parent / "tfs_stability_20260921/study.py",
            ROOT / "PROTOCOL.md",
            ROOT / "native.py",
            ROOT / "analysis.py",
            ROOT / "study_test.py",
        ]
    )
    return sorted(files)


def data_files():
    return sorted(
        p
        for p in Path(load_settings().catalog_dir).rglob("*.parquet")
        if any(
            f"{a}USDT-PERP.BINANCE-1-MINUTE-LAST-EXTERNAL" in str(p)
            or ("crypto_perpetual" in str(p) and f"{a}USDT" in str(p))
            for a in ("BTC", "ETH")
        )
    )


def freeze():
    if (ROOT / "manifest.json").exists():
        raise RuntimeError("Manifest already frozen")
    sources = source_files()
    references = [
        d / f for d in REFERENCES.values() for f in ("trades.csv", "fills.csv", "metrics.json")
    ]
    manifest = {
        "created_at": datetime.now(UTC).isoformat(),
        "start": START.isoformat(),
        "end_exclusive": END.isoformat(),
        "source_sha256": {str(p.relative_to(PROJECT)): digest(p) for p in sources},
        "data_sha256": {str(p.relative_to(PROJECT)): digest(p) for p in data_files()},
        "reference_sha256": {str(p.relative_to(PROJECT)): digest(p) for p in references},
        "environment": environment(),
    }
    json_write(ROOT / "manifest.json", manifest)
    with zipfile.ZipFile(ROOT / "frozen_sources.zip", "w", zipfile.ZIP_DEFLATED) as z:
        for p in sources + [ROOT / "manifest.json"]:
            z.write(p, str(p.relative_to(PROJECT)))
    json_write(ROOT / "archive.json", {"sha256": digest(ROOT / "frozen_sources.zip")})
    print("FROZEN", len(sources), len(manifest["data_sha256"]), flush=True)


def verify():
    manifest = json.loads((ROOT / "manifest.json").read_text())
    for label, files in (("source", source_files()), ("data", data_files())):
        assert {str(p.relative_to(PROJECT)) for p in files} == set(manifest[label + "_sha256"])
    for label in ("source", "data", "reference"):
        for name, sha in manifest[label + "_sha256"].items():
            assert digest(PROJECT / name) == sha, name
    assert environment() == manifest["environment"]
    return manifest


def execute(run_id):
    verify()
    asset, kind = run_id.split("_")
    output = ROOT / "runs" / run_id
    output.mkdir(parents=True, exist_ok=True)
    if (output / "completion.json").exists():
        for name, expected in json.loads((output / "completion.json").read_text()).items():
            assert digest(output / name) == expected
        print("REUSED", run_id, flush=True)
        return
    strategy = strategy_for(asset, kind)
    spec = BacktestSpec(
        instrument_id=strategy.config.instrument_id,
        data_symbol=asset + "USDT",
        bar_type=strategy.config.bar_type,
        interval="1m",
        warmup_days=135 if kind == "MRL" else 21,
        oms_type=OmsType.HEDGING,
        starting_balance=Decimal(500),
        leverage=Decimal(5),
        prepare_data_before_run=False,
    )
    catalog = ParquetDataCatalog(str(load_settings().catalog_dir))
    instrument = max(
        catalog.instruments(instrument_ids=[str(spec.instrument_id)]), key=lambda x: x.ts_init
    )
    json_write(
        output / "parameters.json",
        {
            "manifest_sha256": digest(ROOT / "manifest.json"),
            "config": json.loads(strategy.config.json()),
            "instrument": instrument.to_dict(instrument),
            "warmup_days": spec.warmup_days,
            "starting_balance": 500,
            "leverage": 5,
        },
    )
    engine = BacktestEngine(config=BacktestEngineConfig(logging=LoggingConfig(bypass_logging=True)))
    market = []
    began = time.monotonic()
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
        cursor = START - timedelta(days=spec.warmup_days)
        while cursor < END:
            stop = min(cursor + timedelta(days=90), END)
            _, bars = load_binance_data(cursor, stop, spec, check_data=False)
            if bars:
                market.append(
                    pd.DataFrame(
                        {
                            "ts": [b.ts_event for b in bars],
                            "open": [float(b.open) for b in bars],
                            "high": [float(b.high) for b in bars],
                            "low": [float(b.low) for b in bars],
                            "close": [float(b.close) for b in bars],
                        }
                    )
                )
                engine.add_data(bars)
                engine.run(streaming=True)
                engine.clear_data()
            cursor = stop
            print("BATCH", run_id, stop.isoformat(), flush=True)
        engine.end()
        frame = pd.concat(market, ignore_index=True)
        assert frame.ts.is_monotonic_increasing and frame.ts.is_unique
        if kind == "MRL":
            frame.to_parquet(output / "market.parquet", index=False)
        result = measure(
            engine,
            pd.Series(frame.close.to_numpy(), index=pd.to_datetime(frame.ts, utc=True)),
            START,
            END,
            output,
            500,
        )
        pd.DataFrame(strategy.observations).to_parquet(output / "decisions.parquet", index=False)
        fills = engine.trader.generate_order_fills_report()
        entries = fills[~fills.is_reduce_only.astype(bool)]
        invalid = sum(
            not opening_order_allowed(
                instrument, Decimal(str(f.filled_qty)), Decimal(str(f.avg_px))
            )
            for f in entries.itertuples()
        )
        assert invalid == 0 and not engine.cache.positions_open()
        reasons = Counter()
        off_tick = 0
        for order in engine.cache.orders():
            for field in ("price", "trigger_price"):
                value = getattr(order, field, None)
                if (
                    value is not None
                    and value.as_decimal() % instrument.price_increment.as_decimal()
                ):
                    off_tick += 1
            for event in order.events:
                if reason := getattr(event, "reason", None):
                    reasons[type(event).__name__ + ": " + str(reason)] += 1
        assert off_tick == 0
        if run_id in REFERENCES:
            old = pd.read_csv(REFERENCES[run_id] / "trades.csv")
            new = pd.read_csv(output / "trades.csv")
            pd.testing.assert_frame_equal(old, new, check_exact=False, rtol=1e-10, atol=1e-9)
            old_f = pd.read_csv(REFERENCES[run_id] / "fills.csv")
            new_f = pd.read_csv(output / "fills.csv")
            cols = [
                "type",
                "side",
                "filled_qty",
                "avg_px",
                "commissions",
                "is_reduce_only",
                "ts_last",
            ]
            pd.testing.assert_frame_equal(
                old_f[cols], new_f[cols], check_exact=False, rtol=1e-10, atol=1e-9
            )
            result["baseline_trade_and_fill_reconciliation"] = True
        result.update(
            entry_fills_outside_constraints=invalid,
            off_tick_order_price_fields=off_tick,
            order_event_reasons=dict(reasons),
            observations=len(strategy.observations),
            elapsed_seconds=time.monotonic() - began,
        )
        verify()
        json_write(output / "metrics.json", result)
        json_write(
            output / "completion.json",
            {p.name: digest(p) for p in sorted(output.iterdir()) if p.name != "completion.json"},
        )
        print("DONE", run_id, result["net_pnl"], result["trades"], flush=True)
    finally:
        engine.dispose()


def run_all():
    verify()
    (ROOT / "logs").mkdir(exist_ok=True)

    def child(run_id):
        env = dict(os.environ, OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1", MKL_NUM_THREADS="1")
        with (ROOT / "logs" / (run_id + ".log")).open("w") as log:
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "strategy_python._optimization.btc_eth_signal_attribution_20260921.native",
                    "run",
                    run_id,
                ],
                stdout=log,
                stderr=subprocess.STDOUT,
                env=env,
            )
        if result.returncode:
            raise RuntimeError(run_id + " failed; see log")
        return run_id

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(child, x) for x in IDS]
        for future in as_completed(futures):
            print("COMPLETE", future.result(), flush=True)
    verify()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("freeze", "verify", "all", "run"))
    parser.add_argument("run_id", nargs="?")
    args = parser.parse_args()
    if args.action == "run":
        execute(args.run_id)
    else:
        {"freeze": freeze, "verify": verify, "all": run_all}[args.action]()
