"""Predeclared 2x2 mechanism ablation, native account replay and immutable evidence."""

import argparse
import hashlib
import html
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
from nautilus_trader.model.enums import AccountType, OmsType
from nautilus_trader.model.objects import Money
from nautilus_trader.persistence.catalog import ParquetDataCatalog

from engine.pipeline.config import load_settings
from strategy_python._backtesting import BacktestSpec, load_binance_data
from strategy_python._execution.entry_constraints import opening_order_allowed
from strategy_python._optimization.btc_long_validation_20260921.run import environment
from strategy_python._optimization.tfs_stability_20260921.study import fee_instrument
from strategy_python.mean_reversion_long_regime_5min.preset import VARIANTS, create_strategy

from .accounting import measure

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parents[2]
START = datetime(2021, 1, 1, tzinfo=UTC)
END = datetime(2026, 8, 31, tzinfo=UTC)
PRIOR = ROOT.parent / "btc_eth_signal_attribution_20260921"


def digest(path):
    with Path(path).open("rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()


def write(path, value):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    temporary.replace(path)


def experiments():
    return [
        {
            "id": f"{asset}_{variant}_fee{fee}",
            "asset": asset,
            "variant": variant,
            "extra_fee_bp": fee,
        }
        for variant in VARIANTS
        for fee in (0, 5)
        for asset in ("BTC", "ETH")
    ]


def source_files():
    packages = [
        "mean_reversion_long_regime_5min",
        "btc_cycle_optimized_20260915_mrl_original",
        "_indicator",
        "_mechanism",
        "_risk",
        "_model",
        "_execution",
    ]
    files = {
        p for package in packages for p in (PROJECT / "strategy_python" / package).rglob("*.py")
    }
    files.update((PROJECT / "pipeline").glob("*.py"))
    files.update(
        [
            PROJECT / "engine/pipeline/binance_sources.toml",
            PROJECT / "strategy_python/_backtesting.py",
            ROOT / "run.py",
            ROOT / "accounting.py",
            ROOT / "AMENDMENTS.md",
            ROOT / "PROTOCOL.md",
            ROOT.parent / "tfs_stability_20260921/study.py",
            ROOT.parent / "btc_long_validation_20260921/run.py",
            PROJECT / "tests/strategy_python/mrl_regime_test.py",
        ]
    )
    return sorted(files)


def freeze():
    if (ROOT / "manifest.json").exists():
        raise RuntimeError("This research is already frozen")
    old = json.loads((PRIOR / "manifest.json").read_text())
    # Use exactly the already available local catalog; never download or alter live data.
    data = {name: digest(PROJECT / name) for name in old["data_sha256"]}
    refs = {
        str(p.relative_to(PROJECT)): digest(p)
        for asset in ("BTC", "ETH")
        for p in (PRIOR / "runs" / f"{asset}_MRL").iterdir()
        if p.name in ("fills.csv", "trades.csv", "metrics.json")
    }
    fee_root = ROOT.parent / "btc_long_validation_20260921/runs/mean_reversion_long_fee5"
    refs.update(
        {
            str(p.relative_to(PROJECT)): digest(p)
            for p in fee_root.iterdir()
            if p.name in ("fills.csv", "trades.csv", "metrics.json")
        }
    )
    manifest = {
        "created_at": datetime.now(UTC).isoformat(),
        "start": START.isoformat(),
        "end_exclusive": END.isoformat(),
        "experiments": experiments(),
        "source_sha256": {str(p.relative_to(PROJECT)): digest(p) for p in source_files()},
        "data_sha256": data,
        "reference_sha256": refs,
        "environment": environment(),
        "history_previously_seen": True,
        "live_forward_study_unchanged": True,
    }
    write(ROOT / "manifest.json", manifest)
    with zipfile.ZipFile(ROOT / "frozen_sources.zip", "w", zipfile.ZIP_DEFLATED) as archive:
        for path in source_files() + [ROOT / "manifest.json"]:
            archive.write(path, str(path.relative_to(PROJECT)))
    write(
        ROOT / "archive.json",
        {
            "sha256": digest(ROOT / "frozen_sources.zip"),
            "manifest_sha256": digest(ROOT / "manifest.json"),
        },
    )
    print("FROZEN", len(manifest["source_sha256"]), "sources", len(data), "data files", flush=True)


def verify():
    manifest = json.loads((ROOT / "manifest.json").read_text())
    archive = json.loads((ROOT / "archive.json").read_text())
    if (
        digest(ROOT / "manifest.json") != archive["manifest_sha256"]
        or digest(ROOT / "frozen_sources.zip") != archive["sha256"]
    ):
        raise RuntimeError("Research manifest or source archive changed")
    if {str(p.relative_to(PROJECT)) for p in source_files()} != set(manifest["source_sha256"]):
        raise RuntimeError("Source inventory changed")
    for label in ("source", "data", "reference"):
        for name, expected in manifest[label + "_sha256"].items():
            if digest(PROJECT / name) != expected:
                raise RuntimeError("Frozen input changed: " + name)
    if environment() != manifest["environment"]:
        raise RuntimeError("Python environment changed")
    return manifest


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
        if str(f.type) == "LIMIT":
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
    return {
        "invalid_entry_orders": invalid,
        "off_tick_fields": off_tick,
        "limit_price_improvement_exceptions": improved,
        "order_event_reasons": dict(reasons),
    }


def reconcile(output, asset, fee):
    reference = (
        PRIOR / "runs" / f"{asset}_MRL"
        if fee == 0
        else (
            ROOT.parent / "btc_long_validation_20260921/runs/mean_reversion_long_fee5"
            if asset == "BTC"
            else None
        )
    )
    if reference is None:
        return None
    for filename, cols in (
        ("trades.csv", ["pnl", "entry_time", "exit_time", "notional", "fees", "residual_qty"]),
        (
            "fills.csv",
            ["type", "side", "filled_qty", "avg_px", "commissions", "is_reduce_only", "ts_last"],
        ),
    ):
        pd.testing.assert_frame_equal(
            pd.read_csv(output / filename)[cols],
            pd.read_csv(reference / filename)[cols],
            check_exact=False,
            rtol=1e-10,
            atol=1e-8,
        )
    return {"trade_and_fill_reconciliation": True, "reference": str(reference.relative_to(PROJECT))}


def execute(identity, *, check=False):
    manifest = verify()
    experiment = next(row for row in manifest["experiments"] if row["id"] == identity)
    start, end = (
        (datetime(2022, 5, 1, tzinfo=UTC), datetime(2022, 6, 1, tzinfo=UTC))
        if check
        else (START, END)
    )
    output = ROOT / ("checks" if check else "runs") / identity
    output.mkdir(parents=True, exist_ok=True)
    marker = output / "completion.json"
    if marker.exists():
        for name, expected in json.loads(marker.read_text()).items():
            if digest(output / name) != expected:
                raise RuntimeError("Completed result changed: " + name)
        print("REUSED", identity, flush=True)
        return
    strategy = create_strategy(start, asset=experiment["asset"], variant=experiment["variant"])
    spec = BacktestSpec(
        instrument_id=strategy.config.instrument_id,
        data_symbol=experiment["asset"] + "USDT",
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
        catalog.instruments(instrument_ids=[str(spec.instrument_id)]), key=lambda x: x.ts_init
    )
    instrument = fee_instrument(original, experiment["extra_fee_bp"])
    write(
        output / "parameters.json",
        {
            "experiment": experiment,
            "config": json.loads(strategy.config.json()),
            "instrument": instrument.to_dict(instrument),
            "starting_balance": 500,
            "leverage": 5,
            "warmup_days": 135,
            "start": start.isoformat(),
            "end_exclusive": end.isoformat(),
            "manifest_sha256": digest(ROOT / "manifest.json"),
            "engineering_check": check,
        },
    )
    engine = BacktestEngine(config=BacktestEngineConfig(logging=LoggingConfig(bypass_logging=True)))
    began = time.monotonic()
    marks = []
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
        result.update(
            {
                "experiment": experiment,
                "holding_median_hours": float(duration.median()) if len(duration) else None,
                "holding_max_hours": float(duration.max()) if len(duration) else None,
                "holding_over_7d": int((duration > 7 * 24).sum()),
                "regime_checks": strategy.regime_checks,
                "regime_blocked": strategy.regime_blocked,
                "elapsed_seconds": time.monotonic() - began,
            }
        )
        if not check and experiment["variant"] == "baseline":
            result["baseline_reconciliation"] = reconcile(
                output, experiment["asset"], experiment["extra_fee_bp"]
            )
        fills = engine.trader.generate_order_fills_report()
        tags = fills.tags.fillna("") if "tags" in fills else pd.Series(dtype=str)
        result["exit_order_tags"] = {
            reason: int(tags.str.contains(reason, regex=False).sum())
            for reason in (
                "cycle_time_exit",
                "cycle_structure_exit",
                "cycle_exit_retry",
                "hard_stop",
                "take_profit",
            )
        }
        verify()
        write(output / "metrics.json", result)
        write(
            marker,
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
                {k: result[k] for k in ("net_pnl", "max_drawdown_pct", "trades", "elapsed_seconds")}
            ),
            flush=True,
        )
    finally:
        engine.dispose()


def run_all(workers):
    manifest = verify()
    (ROOT / "logs").mkdir(exist_ok=True)

    def child(row):
        env = dict(os.environ, OPENBLAS_NUM_THREADS="1", OMP_NUM_THREADS="1", MKL_NUM_THREADS="1")
        with (ROOT / "logs" / f"{row['id']}.log").open("a") as log:
            result = subprocess.run(
                [sys.executable, "-m", __spec__.name, "run", row["id"]],
                stdout=log,
                stderr=subprocess.STDOUT,
                env=env,
                check=False,
            )
        if result.returncode:
            raise RuntimeError(row["id"] + " failed; retained log")
        return row["id"]

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(child, row) for row in manifest["experiments"]]
        for future in as_completed(futures):
            print("COMPLETE", future.result(), flush=True)


def decide(results):
    decisions = {}
    for variant in VARIANTS[1:]:
        checks = []
        for asset in ("BTC", "ETH"):
            for fee in (0, 5):
                row = results[f"{asset}_{variant}_fee{fee}"]
                base = results[f"{asset}_baseline_fee{fee}"]
                checks.append(
                    {
                        "asset": asset,
                        "extra_fee_bp": fee,
                        "full_profit": row["net_pnl"] > 0,
                        "recent_profit": (row["review_marked_pnl"] or 0) > 0,
                        "enough_trades": row["trades"] >= 100 and row["review_closed_trades"] >= 30,
                        "drawdown_not_worse": row["max_drawdown_pct"] <= base["max_drawdown_pct"],
                        "execution_valid": row["invalid_entry_orders"]
                        == row["off_tick_fields"]
                        == 0
                        and abs(row["cashflow_error"]) < 1e-4,
                    }
                )
        passed = all(
            all(v for k, v in c.items() if k not in ("asset", "extra_fee_bp")) for c in checks
        )
        decisions[variant] = {"passed": passed, "checks": checks}
    return {
        "main_candidate": "combined",
        "main_passed": decisions["combined"]["passed"],
        "variants": decisions,
        "default_strategy_changed": False,
        "live_trading_authorized": False,
        "decision": "值得独立前向验证"
        if decisions["combined"]["passed"]
        else "主候选未通过，保留研究结果，不替换默认策略",
    }


def report():
    manifest = verify()
    results = {}
    rows = []
    for experiment in manifest["experiments"]:
        output = ROOT / "runs" / experiment["id"]
        for filename, expected in json.loads((output / "completion.json").read_text()).items():
            if digest(output / filename) != expected:
                raise RuntimeError("Result changed")
        result = json.loads((output / "metrics.json").read_text())
        results[experiment["id"]] = result
        rows.append(
            {
                **experiment,
                **{
                    k: result[k]
                    for k in (
                        "net_pnl",
                        "return_pct",
                        "max_drawdown_pct",
                        "trades",
                        "profit_factor",
                        "fees",
                        "review_marked_pnl",
                        "review_closed_trades",
                        "holding_median_hours",
                        "holding_max_hours",
                        "holding_over_7d",
                        "worst5_pnl",
                        "pnl_without_best5",
                        "regime_checks",
                        "regime_blocked",
                        "missing_minutes",
                    )
                },
            }
        )
    table = pd.DataFrame(rows)
    table.to_csv(ROOT / "comparison.csv", index=False)
    decision = decide(results)
    write(ROOT / "decision.json", decision)
    labels = {
        "baseline": "原基线",
        "regime": "仅日线过滤",
        "bounded": "仅退出规则组",
        "combined": "组合主候选",
    }
    lines = [
        "# 均值回归多头机制优化结果",
        "",
        decision["decision"],
        "",
        "BTC/ETH、四组固定规则、两档实际手续费，共16次原生连续账户回放。历史均已见，不是独立样本外验证。",
        "",
        "| 市场 | 规则 | 每次成交额外bp | 净收益USDT | 累计% | 回撤% | 交易数 | 2025后盈亏 | 最长持仓小时 |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in rows:
        lines.append(
            f"| {r['asset']} | {labels[r['variant']]} | {r['extra_fee_bp']} | {r['net_pnl']:.2f} | {r['return_pct']:.2f} | {r['max_drawdown_pct']:.2f} | {r['trades']} | {r['review_marked_pnl']:.2f} | {r['holding_max_hours'] or 0:.2f} |"
        )
    lines += [
        "",
        "## 解读与限制",
        "",
        "主候选在查看结果前固定为组合版本，另外两组只用于拆分过滤与退出规则组的效果。只减少亏损或回撤不等于已经获得正期望。",
        "日线过滤会减少可交易机会；缺口后需59个连续完整日重新预热。时间与布林距离退出为分钟收盘触发，不保证亏损上限。原25%保护止损和原定仓预算保留，未因退出更近放大仓位。",
        "未计历史资金费率、真实盘口/排队及逐日合约规则。5bp每次成交压力实际重跑，但不能代替真实滑点。全部结果含缺失分钟，未补假价格。",
        "当前默认策略及90天前向观察保持原样。本轮通过也只支持另建独立前向验证；失败不触发继续调参。",
        "",
        "[预先方案](PROTOCOL.md) · [完整指标](comparison.csv) · [逐项判断](decision.json) · [图表报告](report.html)",
    ]
    (ROOT / "RESULTS.md").write_text("\n".join(lines) + "\n")
    # Standalone, locally generated scientific figures; no external data requests.
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    figure, axes = plt.subplots(2, 2, figsize=(14, 8), sharex=True)
    for i, asset in enumerate(("BTC", "ETH")):
        for j, fee in enumerate((0, 5)):
            axis = axes[i, j]
            for variant in VARIANTS:
                equity = pd.read_csv(
                    ROOT / "runs" / f"{asset}_{variant}_fee{fee}" / "daily_equity.csv",
                    index_col=0,
                    parse_dates=True,
                )
                axis.plot(equity.index, equity.equity - 500, label=variant, linewidth=1)
            axis.set_title(f"{asset} extra {fee}bp per fill")
            axis.set_ylabel("Net PnL (USDT)")
            axis.axhline(0, color="gray", linewidth=0.5)
            axis.legend(fontsize=8)
            axis.grid(alpha=0.2)
    figure.tight_layout()
    figure.savefig(ROOT / "equity_comparison.png", dpi=160)
    plt.close(figure)
    display = table.copy()
    display["variant"] = display.variant.map(labels)
    page = """<!doctype html><html lang='zh-CN'><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
<title>均值回归多头机制优化</title><style>body{font:16px/1.7 system-ui;margin:30px;color:#243247}table{border-collapse:collapse}td,th{padding:8px;border:1px solid #ccd}img{width:100%;max-width:1400px}</style>"""
    page += "<h1>均值回归多头机制优化</h1><p>" + html.escape(decision["decision"]) + "</p>"
    page += '<p>预先指定组合版本为主候选；BTC/ETH同参数，原费率及每次成交额外5bp实际重跑。全部历史已见，不是盲测。</p><img src="equity_comparison.png" alt="四组规则的累计收益">'
    page += (
        '<div style="overflow:auto">'
        + display.to_html(index=False, float_format=lambda x: f"{x:.2f}")
        + "</div>"
    )
    page += '<p>图为日末权益，表内回撤为分钟盯市。未计资金费率、真实盘口与逐日合约规则；所有候选仅用于研究，默认策略和现有前向观察保持原样。</p><a href="RESULTS.md">文字结论</a> · <a href="PROTOCOL.md">冻结方案</a> · <a href="decision.json">判定明细</a></html>'
    (ROOT / "report.html").write_text(page)
    write(
        ROOT / "final_validation.json",
        {
            "completed_at": datetime.now(UTC).isoformat(),
            "runs": len(results),
            "frozen_inputs_verified": True,
            "baseline_reconciliations": {
                k: v.get("baseline_reconciliation") for k, v in results.items() if "baseline" in k
            },
            "decision": decision,
            "outputs": {
                name: digest(ROOT / name)
                for name in (
                    "comparison.csv",
                    "decision.json",
                    "RESULTS.md",
                    "report.html",
                    "equity_comparison.png",
                )
            },
        },
    )
    print(json.dumps(decision, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("freeze", "verify", "run", "check", "all", "report"))
    parser.add_argument("identity", nargs="?")
    parser.add_argument("--workers", type=int, default=3)
    args = parser.parse_args()
    if args.action in ("run", "check"):
        execute(args.identity, check=args.action == "check")
    elif args.action == "all":
        run_all(args.workers)
    else:
        {"freeze": freeze, "verify": verify, "report": report}[args.action]()
