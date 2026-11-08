"""Fixed entry-quality ablation with native execution and immutable run evidence."""

import argparse
import json
import os
import subprocess
import sys
import time
import zipfile
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

from engine.pipeline.config import load_settings
from strategy_python._backtesting import BacktestSpec, load_binance_data
from strategy_python._optimization.btc_long_validation_20260921.run import environment
from strategy_python._optimization.mrl_regime_20260922.accounting import measure
from strategy_python._optimization.mrl_regime_20260922.run import (
    audit_execution,
    digest,
    write,
)
from strategy_python._optimization.mrl_regime_20260922.run import (
    verify as verify_prior,
)
from strategy_python._optimization.tfs_stability_20260921.study import fee_instrument
from strategy_python.mean_reversion_long_entry_5min.preset import create_strategy

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parents[2]
PRIOR = ROOT.parent / "mrl_regime_20260922"
START = datetime(2021, 1, 1, tzinfo=UTC)
END = datetime(2026, 8, 31, tzinfo=UTC)


def experiments():
    rows = []
    for fee in (0, 5):
        for variant in ("cost", "reclaim"):
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
            "id": f"{asset}_reclaim_fee5_strict",
            "asset": asset,
            "variant": "reclaim",
            "extra_fee_bp": 5,
            "strict": True,
        }
        for asset in ("BTC", "ETH")
    ]
    return rows


def local_sources():
    return sorted(
        [
            *(PROJECT / "strategy_python/mean_reversion_long_entry_5min").glob("*.py"),
            ROOT / "run.py",
            ROOT / "PROTOCOL.md",
            ROOT / "METHODS.md",
            PROJECT / "tests/strategy_python/mrl_entry_test.py",
        ]
    )


def freeze():
    if (ROOT / "manifest.json").exists():
        raise RuntimeError("Research already frozen")
    prior = verify_prior()
    references = {}
    for asset in ("BTC", "ETH"):
        for variant in ("baseline", "bounded"):
            for fee in (0, 5):
                output = PRIOR / "runs" / f"{asset}_{variant}_fee{fee}"
                marker = json.loads((output / "completion.json").read_text())
                for name, expected in marker.items():
                    if digest(output / name) != expected:
                        raise RuntimeError("Prior completed result changed")
                    references[str((output / name).relative_to(PROJECT))] = expected
                references[str((output / "completion.json").relative_to(PROJECT))] = digest(
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
        "reference_sha256": references,
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
        trades = pd.read_csv(output / "trades.csv")
        duration = (
            pd.to_datetime(trades.exit_time, utc=True) - pd.to_datetime(trades.entry_time, utc=True)
        ).dt.total_seconds() / 3600
        filled = engine.trader.generate_order_fills_report()
        entry_fills = filled[~filled.is_reduce_only] if not filled.empty else filled
        if row["variant"] == "reclaim" and len(entry_fills):
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
            reference = PRIOR / "revisions/v1/checks/BTC_bounded_fee0"
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
        if row["variant"] != "reclaim":
            continue
        m = results[row["id"]]
        b = baselines[f"{row['asset']}_baseline_fee{row['extra_fee_bp']}"]
        checks.append(
            {
                "id": row["id"],
                "strict": row["strict"],
                "full_positive": m["net_pnl"] > 0,
                "recent_positive": (m["review_marked_pnl"] or 0) > 0,
                "sufficient": m["trades"] >= 100 and m["review_closed_trades"] >= 30,
                "diversified": m["pnl_without_best5"] > 0,
                "drawdown_ok": row["strict"] or m["max_drawdown_pct"] <= b["max_drawdown_pct"],
                "execution_ok": m["invalid_entry_orders"] == m["off_tick_fields"] == 0
                and abs(m["cashflow_error"]) < 1e-4,
                "improvement": m["net_pnl"] > b["net_pnl"]
                and m["review_marked_pnl"] > b["review_marked_pnl"]
                and m["max_drawdown_pct"] <= b["max_drawdown_pct"],
            }
        )
    passed = all(
        all(v for k, v in r.items() if k not in ("id", "strict", "improvement")) for r in checks
    )
    improvement = all(
        r["improvement"] and r["sufficient"] and r["execution_ok"]
        for r in checks
        if not r["strict"]
    )
    return {
        "main_candidate": "reclaim",
        "passed": passed,
        "consistent_improvement": improvement,
        "checks": checks,
        "decision": "值得独立前向验证"
        if passed
        else ("一致改善但未达到盈利证据门槛" if improvement else "未形成跨市场一致改进"),
        "historical_research_only": True,
        "default_changed": False,
    }


def report():
    manifest = verify()
    results = {r["id"]: verify_result(ROOT / "runs" / r["id"]) for r in manifest["experiments"]}
    baselines = {
        f"{a}_{v}_fee{f}": verify_result(PRIOR / "runs" / f"{a}_{v}_fee{f}")
        for a in ("BTC", "ETH")
        for v in ("baseline", "bounded")
        for f in (0, 5)
    }
    verdict = decision(results, baselines)
    write(ROOT / "decision.json", verdict)
    rows = []
    for identity, m in {**baselines, **results}.items():
        r = {**m["experiment"], "id": identity}
        r.update(
            {
                k: m[k]
                for k in (
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
            }
        )
        rows.append(r)
    table = pd.DataFrame(rows)
    table["strict"] = table["strict"].fillna(False)
    table.to_csv(ROOT / "comparison.csv", index=False)
    labels = {
        "baseline": "原策略",
        "bounded": "仅退出对照",
        "cost": "成本门槛",
        "reclaim": "成本＋收复确认",
    }
    lines = [
        "# 入场质量优化结果",
        "",
        verdict["decision"],
        "",
        "新增10次完整原生回放，另复用并核验8组已冻结对照。历史已见，不是独立样本外。",
        "初始500 USDT；UTC [2021-01-01,2026-08-31)，2026年截至8月30日。",
        "",
        "| 市场 | 规则 | 额外bp/成交 | 严格成交 | 净收益USDT | 累计% | 回撤% | 笔数 | 2025后净收益 |",
        "|---|---|---:|---|---:|---:|---:|---:|---:|",
    ]
    for r in table.to_dict("records"):
        lines.append(
            f"| {r['asset']} | {labels[r['variant']]} | {r['extra_fee_bp']} | {'是' if r['strict'] else '否'} | {r['net_pnl']:.2f} | {r['return_pct']:.2f} | {r['max_drawdown_pct']:.2f} | {r['trades']} | {r['review_marked_pnl']:.2f} |"
        )
    lines += [
        "",
        "判定同时要求双市场、近期、成本压力、样本量和严格成交支持。减少亏损不等于盈利；不选取最好单组替换主候选。",
        "成本预留用于入场门槛，并未冒充已经发生的费用；5bp压力实际重跑。未计资金费率、真实盘口/排队或逐日合约规则，保留缺失分钟。",
        "",
        "[方法依据](METHODS.md) · [冻结方案](PROTOCOL.md) · [判定明细](decision.json) · [全部指标](comparison.csv)",
    ]
    (ROOT / "RESULTS.md").write_text("\n".join(lines) + "\n")
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(2, 2, figsize=(14, 8), sharex=True)
    for i, asset in enumerate(("BTC", "ETH")):
        for j, fee in enumerate((0, 5)):
            ax = axes[i, j]
            for variant in ("baseline", "bounded", "cost", "reclaim"):
                base = PRIOR if variant in ("baseline", "bounded") else ROOT
                equity = pd.read_csv(
                    base / "runs" / f"{asset}_{variant}_fee{fee}" / "daily_equity.csv",
                    index_col=0,
                    parse_dates=True,
                )
                ax.plot(equity.index, equity.equity - 500, label=variant, lw=1)
            if fee == 5:
                equity = pd.read_csv(
                    ROOT / "runs" / f"{asset}_reclaim_fee5_strict" / "daily_equity.csv",
                    index_col=0,
                    parse_dates=True,
                )
                ax.plot(equity.index, equity.equity - 500, label="reclaim strict", ls="--", lw=1)
            ax.axhline(0, color="gray", lw=0.5)
            ax.set_title(f"{asset} extra {fee}bp per fill")
            ax.set_ylabel("Net PnL (USDT)")
            ax.grid(alpha=0.2)
            ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(ROOT / "equity_comparison.png", dpi=160)
    plt.close(fig)
    page = "<!doctype html><html lang='zh-CN'><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>入场质量优化</title><style>body{font:16px/1.7 system-ui;margin:30px;color:#243247}img{width:100%;max-width:1400px}td,th{padding:8px;border:1px solid #ccd}table{border-collapse:collapse}</style>"
    page += (
        "<h1>入场质量优化</h1><p>"
        + verdict["decision"]
        + '</p><p>500 USDT连续账户；历史已见，不是盲测。图为日末权益，回撤按分钟盯市。</p><img src="equity_comparison.png" alt="各规则收益对照">'
    )
    page += (
        '<div style="overflow:auto">'
        + table.to_html(index=False, float_format=lambda v: f"{v:.2f}")
        + '</div><p>未计资金费率与真实盘口排队。严格版本仅是成交敏感性假设。</p><a href="RESULTS.md">结果</a> · <a href="METHODS.md">方法依据</a> · <a href="PROTOCOL.md">冻结方案</a></html>'
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
                n: digest(ROOT / n)
                for n in (
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
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    if args.action in ("check", "run"):
        execute(args.identity, check=args.action == "check")
    elif args.action == "all":
        run_all(args.workers)
    else:
        {"freeze": freeze, "verify": verify, "report": report}[args.action]()
