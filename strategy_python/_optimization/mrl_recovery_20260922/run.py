"""Fixed entry-quality ablation with native execution and immutable run evidence."""

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
from nautilus_trader.backtest.models import FillModel
from nautilus_trader.config import BacktestEngineConfig, LoggingConfig
from nautilus_trader.model.enums import AccountType, OmsType
from nautilus_trader.model.objects import Money
from nautilus_trader.persistence.catalog import ParquetDataCatalog

from pipeline.config import load_settings
from strategy_python._backtesting import BacktestSpec, load_binance_data
from strategy_python._execution.entry_constraints import opening_order_allowed
from strategy_python._optimization.btc_long_validation_20260921.run import environment
from strategy_python._optimization.mrl_entry_20260922.run import (
    verify as verify_prior,
)
from strategy_python._optimization.mrl_regime_20260922.accounting import measure
from strategy_python._optimization.mrl_regime_20260922.run import (
    digest,
    write,
)
from strategy_python._optimization.tfs_stability_20260921.study import fee_instrument
from strategy_python.mean_reversion_long_recovery_5min.preset import create_strategy

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parents[2]
PRIOR = ROOT.parent / "mrl_entry_20260922"
BASELINE = ROOT.parent / "mrl_regime_20260922"
START = datetime(2021, 1, 1, tzinfo=UTC)
END = datetime(2026, 8, 31, tzinfo=UTC)


def experiments():
    rows = []
    for fee in (0, 5):
        for variant in ("recovery",):
            for asset in ("BTC", "ETH"):
                rows.append(
                    {
                        "id": f"{asset}_{variant}_fee{fee}",
                        "asset": asset,
                        "variant": variant,
                        "extra_fee_bp": fee,
                        "strict": False,
                    }
                )
    rows += [
        {
            "id": f"{asset}_recovery_fee5_strict",
            "asset": asset,
            "variant": "recovery",
            "extra_fee_bp": 5,
            "strict": True,
        }
        for asset in ("BTC", "ETH")
    ]
    return rows


def references():
    paths = {}
    for asset in ("BTC", "ETH"):
        for fee in (0, 5):
            for variant, base in (("baseline", BASELINE), ("reclaim", PRIOR)):
                identity = f"{asset}_{variant}_fee{fee}"
                paths[identity] = base / "runs" / identity
        identity = f"{asset}_reclaim_fee5_strict"
        paths[identity] = PRIOR / "runs" / identity
    return paths


def audit_execution(engine, instrument):
    fills = engine.trader.generate_order_fills_report()
    reasons = Counter()
    invalid = off_tick = improved = 0
    for f in fills.itertuples():
        if bool(f.is_reduce_only):
            continue
        quantity = Decimal(str(f.quantity))
        fill_quantity = Decimal(str(f.filled_qty))
        fill_price = Decimal(str(f.avg_px))
        if str(f.type) in ("LIMIT", "STOP_LIMIT"):
            price = Decimal(str(f.price))
            valid = opening_order_allowed(instrument, quantity, price)
            invalid += not valid
            if not opening_order_allowed(instrument, fill_quantity, fill_price):
                if not (
                    valid
                    and str(f.side) == "BUY"
                    and fill_price <= price
                    and quantity == fill_quantity
                ):
                    raise RuntimeError("Unexplained fill outside order bounds")
                improved += 1
        else:
            invalid += not opening_order_allowed(instrument, fill_quantity, fill_price)
    for order in engine.cache.orders():
        for field in ("price", "trigger_price"):
            price = getattr(order, field, None)
            if price is not None and price.as_decimal() % instrument.price_increment.as_decimal():
                off_tick += 1
        for event in order.events:
            if reason := getattr(event, "reason", None):
                reasons[type(event).__name__ + ": " + str(reason)] += 1
    if invalid or off_tick or engine.cache.positions_open():
        raise RuntimeError(f"Invalid replay: orders={invalid}, off_tick={off_tick}, open positions")
    result = {
        "invalid_entry_orders": invalid,
        "off_tick_fields": off_tick,
        "limit_price_improvement_exceptions": improved,
        "order_event_reasons": dict(reasons),
    }

    checked = 0
    for order in engine.cache.orders():
        if order.is_reduce_only:
            continue
        checked += 1
        if order.order_type.name != "LIMIT" or order.time_in_force.name != "GTD":
            raise RuntimeError("Expected expiring price-capped entry")
        for fill in (e for e in order.events if type(e).__name__ == "OrderFilled"):
            if fill.ts_event < order.ts_init or fill.ts_event >= order.expire_time_ns:
                raise RuntimeError("Entry filled outside its valid interval")
            if fill.last_px.as_decimal() > order.price.as_decimal():
                raise RuntimeError("Entry fill exceeded its price cap")
    result["price_capped_entry_orders"] = checked
    return result


def local_sources():
    return sorted(
        [
            *(PROJECT / "strategy_python/mean_reversion_long_recovery_5min").glob("*.py"),
            ROOT / "run.py",
            ROOT / "diagnostics.py",
            ROOT / "PROTOCOL.md",
            ROOT / "METHODS.md",
            PROJECT / "tests/strategy_python/mrl_recovery_test.py",
        ]
    )


def freeze():
    if (ROOT / "manifest.json").exists():
        raise RuntimeError("Research already frozen")
    prior = verify_prior()
    reference_hashes = {}
    for output in references().values():
        marker = json.loads((output / "completion.json").read_text())
        for name, expected in marker.items():
            if digest(output / name) != expected:
                raise RuntimeError("Prior completed result changed")
            reference_hashes[str((output / name).relative_to(PROJECT))] = expected
        reference_hashes[str((output / "completion.json").relative_to(PROJECT))] = digest(
            output / "completion.json"
        )
    sources = {str(p.relative_to(PROJECT)): digest(p) for p in local_sources()}
    sources.update(prior["source_sha256"])
    manifest = {
        "created_at": datetime.now(UTC).isoformat(),
        "experiments": experiments(),
        "source_sha256": sources,
        "local_source_inventory": [str(p.relative_to(PROJECT)) for p in local_sources()],
        "data_sha256": prior["data_sha256"],
        "reference_sha256": reference_hashes,
        "environment": environment(),
        "history_previously_seen": True,
        "prior_manifest_sha256": digest(PRIOR / "manifest.json"),
    }
    write(ROOT / "manifest.json", manifest)
    with zipfile.ZipFile(ROOT / "frozen_sources.zip", "w", zipfile.ZIP_DEFLATED) as archive:
        for name in sorted(sources):
            archive.write(PROJECT / name, name)
        archive.write(ROOT / "manifest.json", str((ROOT / "manifest.json").relative_to(PROJECT)))
    write(
        ROOT / "archive.json",
        {
            "manifest_sha256": digest(ROOT / "manifest.json"),
            "source_archive_sha256": digest(ROOT / "frozen_sources.zip"),
        },
    )
    print("FROZEN", len(sources), "sources", len(manifest["data_sha256"]), "data files", flush=True)


def verify():
    manifest = json.loads((ROOT / "manifest.json").read_text())
    archive = json.loads((ROOT / "archive.json").read_text())
    if (
        digest(ROOT / "manifest.json") != archive["manifest_sha256"]
        or digest(ROOT / "frozen_sources.zip") != archive["source_archive_sha256"]
    ):
        raise RuntimeError("Manifest or archive changed")
    if [str(p.relative_to(PROJECT)) for p in local_sources()] != manifest["local_source_inventory"]:
        raise RuntimeError("Source inventory changed")
    for label in ("source", "data", "reference"):
        for name, expected in manifest[label + "_sha256"].items():
            if digest(PROJECT / name) != expected:
                raise RuntimeError("Frozen input changed: " + name)
    if (
        environment() != manifest["environment"]
        or digest(PRIOR / "manifest.json") != manifest["prior_manifest_sha256"]
    ):
        raise RuntimeError("Environment or prior manifest changed")
    return manifest


def verify_result(output):
    for name, expected in json.loads((output / "completion.json").read_text()).items():
        if digest(output / name) != expected:
            raise RuntimeError("Completed result changed: " + str(output / name))
    return json.loads((output / "metrics.json").read_text())


def execute(identity, *, check=False):
    if not check:
        manifest = verify()
        row = next(r for r in manifest["experiments"] if r["id"] == identity)
    else:
        asset, variant, fee = identity.split("_")
        row = {
            "id": identity,
            "asset": asset,
            "variant": variant,
            "extra_fee_bp": int(fee[3:]),
            "strict": False,
        }
    start, end = (
        (datetime(2022, 5, 1, tzinfo=UTC), datetime(2022, 6, 1, tzinfo=UTC))
        if check
        else (START, END)
    )
    output = ROOT / ("checks" if check else "runs") / identity
    output.mkdir(parents=True, exist_ok=True)
    if (output / "completion.json").exists():
        verify_result(output)
        recorded = json.loads((output / "parameters.json").read_text())
        current_sources = {str(p.relative_to(PROJECT)): digest(p) for p in local_sources()}
        if recorded["source_sha256"] != current_sources:
            raise RuntimeError("Completed run uses different source; preserve it before rerunning")
        print("REUSED", identity, flush=True)
        return
    strategy = create_strategy(start, asset=row["asset"], variant=row["variant"])
    spec = BacktestSpec(
        instrument_id=strategy.config.instrument_id,
        data_symbol=row["asset"] + "USDT",
        bar_type=strategy.config.bar_type,
        interval="1m",
        warmup_days=135,
        oms_type=OmsType.HEDGING,
        starting_balance=Decimal(500),
        leverage=Decimal(5),
        prepare_data_before_run=False,
    )
    catalog = ParquetDataCatalog(str(load_settings().catalog_dir))
    original = max(
        catalog.instruments(instrument_ids=[str(spec.instrument_id)]), key=lambda i: i.ts_init
    )
    instrument = fee_instrument(original, row["extra_fee_bp"])
    write(
        output / "parameters.json",
        {
            "experiment": row,
            "config": json.loads(strategy.config.json()),
            "instrument": instrument.to_dict(instrument),
            "start": start.isoformat(),
            "end_exclusive": end.isoformat(),
            "source_sha256": {str(p.relative_to(PROJECT)): digest(p) for p in local_sources()},
            "manifest_sha256": digest(ROOT / "manifest.json") if not check else None,
            "initial_balance": 500,
            "leverage": 5,
            "warmup_days": 135,
            "engineering_check": check,
        },
    )
    engine = BacktestEngine(config=BacktestEngineConfig(logging=LoggingConfig(bypass_logging=True)))
    marks = []
    began = time.monotonic()
    try:
        engine.add_venue(
            venue=BINANCE_VENUE,
            oms_type=OmsType.HEDGING,
            account_type=AccountType.MARGIN,
            base_currency=None,
            starting_balances=[Money(500, instrument.quote_currency)],
            default_leverage=Decimal(5),
            fill_model=FillModel(
                prob_fill_on_limit=0.0 if row["strict"] else 1.0,
                prob_slippage=1.0 if row["strict"] else 0.0,
                random_seed=20260922,
            ),
        )
        engine.add_instrument(instrument)
        engine.add_strategy(strategy)
        cursor = start - timedelta(days=135)
        while cursor < end:
            stop = min(cursor + timedelta(days=90), end)
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
            print("BATCH", identity, stop.isoformat(), flush=True)
            cursor = stop
        engine.end()
        result = measure(engine, pd.concat(marks), start, end, output, 500)
        result.update(audit_execution(engine, instrument))
        engine.trader.generate_orders_report().to_csv(output / "orders.csv")
        trades = pd.read_csv(output / "trades.csv")
        duration = (
            pd.to_datetime(trades.exit_time, utc=True) - pd.to_datetime(trades.entry_time, utc=True)
        ).dt.total_seconds() / 3600
        filled = engine.trader.generate_order_fills_report()
        entry_fills = filled[~filled.is_reduce_only] if not filled.empty else filled
        decisions = pd.DataFrame(
            strategy.recovery_decisions,
            columns=(
                "signal_created_ns",
                "decision_ns",
                "expires_ns",
                "confirmation_high",
                "decision_close",
                "limit_price",
                "target_middle",
            ),
        )
        decisions.to_csv(output / "entry_decisions.csv", index=False)
        if row["variant"] == "recovery" and len(entry_fills):
            if decisions.decision_ns.duplicated().any():
                raise RuntimeError("Multiple entries reused one confirmation timestamp")
            by_time = decisions.set_index("decision_ns")
            for fill in entry_fills.itertuples():
                d = by_time.loc[pd.Timestamp(fill.ts_init).value]
                if not (
                    d.signal_created_ns < pd.Timestamp(fill.ts_init).value < d.expires_ns
                    and d.decision_close > d.confirmation_high
                    and d.decision_close <= d.limit_price
                    and float(fill.avg_px) <= d.limit_price + 1e-7 < d.target_middle
                ):
                    raise RuntimeError("Entry violates completed-minute confirmation or price cap")
        if len(entry_fills):
            if not (entry_fills.time_in_force.astype(str) == "GTD").all():
                raise RuntimeError("Reclaim entry without native expiry")
            expiry = pd.to_numeric(entry_fills.expire_time_ns)
            # DateTime pandas dtype may use us instead of ns; explicitly normalize.
            times = pd.DatetimeIndex(entry_fills.ts_last).as_unit("ns").asi8
            if (times >= expiry.to_numpy()).any():
                raise RuntimeError("Entry filled after confirmation expiry")
        result.update(
            experiment=row,
            entry_admission=dict(strategy.entry_stats),
            holding_max_hours=float(duration.max()) if len(duration) else None,
            holding_median_hours=float(duration.median()) if len(duration) else None,
            elapsed_seconds=time.monotonic() - began,
        )
        if (
            check
            and row["variant"] == "control"
            and row["asset"] == "BTC"
            and row["extra_fee_bp"] == 0
        ):
            reference = PRIOR / "checks/BTC_reclaim_fee0"
            for name, cols in [
                (
                    "trades.csv",
                    ["pnl", "entry_time", "exit_time", "notional", "fees", "residual_qty"],
                ),
                (
                    "fills.csv",
                    [
                        "type",
                        "side",
                        "filled_qty",
                        "avg_px",
                        "commissions",
                        "is_reduce_only",
                        "ts_last",
                    ],
                ),
            ]:
                pd.testing.assert_frame_equal(
                    pd.read_csv(output / name)[cols],
                    pd.read_csv(reference / name)[cols],
                    check_exact=False,
                    rtol=1e-10,
                    atol=1e-8,
                )
            result["control_reconciliation"] = True
        if not check:
            verify()
        write(output / "metrics.json", result)
        write(
            output / "completion.json",
            {
                p.name: digest(p)
                for p in output.iterdir()
                if p.is_file() and p.name != "completion.json"
            },
        )
        print(
            "DONE",
            identity,
            json.dumps(
                {k: result[k] for k in ("net_pnl", "trades", "max_drawdown_pct", "elapsed_seconds")}
            ),
            flush=True,
        )
    finally:
        engine.dispose()


def run_all(workers):
    manifest = verify()
    (ROOT / "logs").mkdir(exist_ok=True)

    def child(row):
        with (ROOT / "logs" / f"{row['id']}.log").open("a") as log:
            result = subprocess.run(
                [sys.executable, "-m", __spec__.name, "run", row["id"]],
                stdout=log,
                stderr=subprocess.STDOUT,
                check=False,
                env=dict(
                    os.environ, OPENBLAS_NUM_THREADS="1", OMP_NUM_THREADS="1", MKL_NUM_THREADS="1"
                ),
            )
        if result.returncode:
            raise RuntimeError(row["id"] + " failed; log retained")
        return row["id"]

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(child, row) for row in manifest["experiments"]]
        for future in as_completed(futures):
            print("COMPLETE", future.result(), flush=True)


def decision(results, baselines):
    checks = []
    for row in experiments():
        m = results[row["id"]]
        peers = [
            baselines[f"{row['asset']}_{variant}_fee{row['extra_fee_bp']}"]
            for variant in ("baseline", "reclaim")
        ]
        checks.append(
            {
                "id": row["id"],
                "strict": row["strict"],
                "full_positive": m["net_pnl"] > 0,
                "recent_positive": (m["review_marked_pnl"] or 0) > 0,
                "sufficient": m["trades"] >= 100 and m["review_closed_trades"] >= 30,
                "diversified": m["pnl_without_best5"] > 0,
                "drawdown_ok": row["strict"]
                or all(m["max_drawdown_pct"] <= p["max_drawdown_pct"] for p in peers),
                "execution_ok": m["invalid_entry_orders"] == m["off_tick_fields"] == 0
                and abs(m["cashflow_error"]) < 1e-4,
                "improvement": all(
                    m["net_pnl"] > p["net_pnl"]
                    and m["review_marked_pnl"] > p["review_marked_pnl"]
                    and m["max_drawdown_pct"] <= p["max_drawdown_pct"]
                    for p in peers
                ),
            }
        )
    passed = all(
        all(v for k, v in r.items() if k not in ("id", "strict", "improvement")) for r in checks
    )
    improved = all(
        r["improvement"] and r["sufficient"] and r["execution_ok"]
        for r in checks
        if not r["strict"]
    )
    return {
        "main_candidate": "recovery",
        "passed": passed,
        "consistent_improvement": improved,
        "checks": checks,
        "decision": "值得独立前向验证"
        if passed
        else ("一致改善但未达到盈利证据门槛" if improved else "未形成跨市场一致改进"),
        "historical_research_only": True,
        "default_changed": False,
    }


def report():
    manifest = verify()
    results = {r["id"]: verify_result(ROOT / "runs" / r["id"]) for r in manifest["experiments"]}
    baselines = {name: verify_result(path) for name, path in references().items()}
    verdict = decision(results, baselines)
    write(ROOT / "decision.json", verdict)
    columns = (
        "net_pnl",
        "return_pct",
        "max_drawdown_pct",
        "trades",
        "fees",
        "review_marked_pnl",
        "review_closed_trades",
        "pnl_without_best5",
        "holding_max_hours",
        "missing_minutes",
    )
    rows = [
        {**m["experiment"], "id": identity, **{k: m[k] for k in columns}}
        for identity, m in {**baselines, **results}.items()
    ]
    table = pd.DataFrame(rows)
    table["strict"] = table["strict"].fillna(False)
    table.to_csv(ROOT / "comparison.csv", index=False)
    labels = {"baseline": "原策略", "reclaim": "上一轮回踩", "recovery": "收复后突破"}
    lines = [
        "# 收复后突破研究结果",
        "",
        verdict["decision"],
        "",
        "6组新增原生回放，10组已冻结参考；初始500 USDT，UTC [2021-01-01,2026-08-31)。全部历史已经参与研发。",
        "",
        "| 市场 | 规则 | 额外bp/成交 | 严格成交 | 净收益USDT | 累计% | 最大回撤% | 笔数 | 2025后净收益 |",
        "|---|---|---:|---|---:|---:|---:|---:|---:|",
    ]
    for r in table.to_dict("records"):
        lines.append(
            f"| {r['asset']} | {labels[r['variant']]} | {r['extra_fee_bp']} | {'是' if r['strict'] else '否'} | {r['net_pnl']:.2f} | {r['return_pct']:.2f} | {r['max_drawdown_pct']:.2f} | {r['trades']} | {r['review_marked_pnl']:.2f} |"
        )
    lines += [
        "",
        "交易成本按实际费率重跑，门槛和交易路径会变化；严格成交不是损失上界。未计资金费率、真实盘口与队列、逐日合约规则；分钟缺口未补齐。",
        "",
        "[固定方案](PROTOCOL.md) · [方法依据](METHODS.md) · [判定明细](decision.json)",
    ]
    (ROOT / "RESULTS.md").write_text("\n".join(lines) + "\n")
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(2, 2, figsize=(14, 8), sharex=True)
    for i, asset in enumerate(("BTC", "ETH")):
        for j, fee in enumerate((0, 5)):
            ax = axes[i, j]
            for variant, base in (("baseline", BASELINE), ("reclaim", PRIOR), ("recovery", ROOT)):
                equity = pd.read_csv(
                    base / "runs" / f"{asset}_{variant}_fee{fee}" / "daily_equity.csv",
                    index_col=0,
                    parse_dates=True,
                )
                ax.plot(equity.index, equity.equity - 500, label=variant, lw=1)
            if fee == 5:
                equity = pd.read_csv(
                    ROOT / "runs" / f"{asset}_recovery_fee5_strict" / "daily_equity.csv",
                    index_col=0,
                    parse_dates=True,
                )
                ax.plot(equity.index, equity.equity - 500, label="recovery strict", ls="--", lw=1)
            ax.axhline(0, color="gray", lw=0.5)
            ax.set_title(f"{asset} extra {fee}bp per fill")
            ax.set_ylabel("Net PnL (USDT)")
            ax.grid(alpha=0.2)
            ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(ROOT / "equity_comparison.png", dpi=160)
    plt.close(fig)
    page = "<!doctype html><html lang='zh-CN'><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>收复后突破研究</title><style>body{font:16px/1.7 system-ui;margin:30px;color:#243247}img{width:100%;max-width:1400px}td,th{padding:8px;border:1px solid #ccd}table{border-collapse:collapse}</style>"
    page += f"<h1>收复后突破研究</h1><p>{verdict['decision']}</p><p>500 USDT连续账户；已见历史，非独立样本外。曲线为日末权益，最大回撤按分钟盯市。</p><img src='equity_comparison.png' alt='三代策略对照'>"
    page += (
        '<div style="overflow:auto">'
        + table.to_html(index=False, float_format=lambda v: f"{v:.2f}")
        + '</div><p>未计资金费率和真实盘口队列。严格成交是敏感性假设。</p><a href="INTERPRETATION.md">结果解释</a> · <a href="PROTOCOL.md">方案</a></html>'
    )
    (ROOT / "report.html").write_text(page)
    write(
        ROOT / "final_validation.json",
        {
            "completed_at": datetime.now(UTC).isoformat(),
            "formal_runs": len(results),
            "reference_runs": len(baselines),
            "frozen_inputs_verified": True,
            "decision": verdict,
            "outputs": {
                name: digest(ROOT / name)
                for name in (
                    "RESULTS.md",
                    "decision.json",
                    "comparison.csv",
                    "report.html",
                    "equity_comparison.png",
                )
            },
        },
    )
    print(json.dumps(verdict, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("freeze", "verify", "check", "run", "all", "report"))
    parser.add_argument("identity", nargs="?")
    parser.add_argument("--workers", type=int, default=3)
    args = parser.parse_args()
    if args.action in ("check", "run"):
        execute(args.identity, check=args.action == "check")
    elif args.action == "all":
        run_all(args.workers)
    else:
        {"freeze": freeze, "verify": verify, "report": report}[args.action]()
