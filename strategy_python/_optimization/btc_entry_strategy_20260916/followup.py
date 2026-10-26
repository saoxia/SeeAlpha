"""Finite time/cap follow-up after the first-stage failure of stop additions."""

import hashlib
import json
from pathlib import Path

import pandas as pd

from strategy_python.btc_failed_breakout_20260916.strategy import Config, signals, simulate

from .run import DATA, PERIODS

ROOT = Path(__file__).resolve().parent


def main():
    minute = pd.read_parquet(DATA / "BTC_minutes.parquet")
    signal = signals(pd.read_parquet(DATA / "BTC_60m.parquet"))
    rows = []

    def execute(stage, config, period, label, **kwargs):
        start, end = [pd.Timestamp(x, tz="UTC") for x in PERIODS[period]]
        m, t, d = simulate(minute, signal, config, start, end, **kwargs)
        path = ROOT / "followup_runs" / stage / label / period
        path.mkdir(parents=True, exist_ok=True)
        t.to_csv(path / "trades.csv", index=False)
        d.to_csv(path / "daily_equity.csv", header=["equity"])
        (path / "metrics.json").write_text(json.dumps(m, indent=2) + "\n")
        rows.append({"stage": stage, "config": config.name, "label": label, "period": period, **m})
        print(
            stage,
            label,
            period,
            round(m["net_pnl"], 3),
            round(m["max_drawdown_pct"], 3),
            flush=True,
        )

    configs = [Config(f"time{h}", h) for h in [36, 48, 60]]
    for c in configs:
        for period in PERIODS:
            execute("time", c, period, c.name)
    table = pd.DataFrame(rows)
    dev = table[table.period == "develop"]
    base = dev[dev.label == "time48"].iloc[0]
    eligible = dev[(dev.net_pnl > 0) & (dev.max_drawdown_pct <= base.max_drawdown_pct)]
    name = (
        eligible.sort_values("net_pnl", ascending=False).iloc[0].config
        if len(eligible)
        else "time48"
    )
    config = next(c for c in configs if c.name == name)
    caps = {0.2, 0.25, 0.3, 0.5, 1.0}
    for cap in sorted(caps):
        for period in PERIODS:
            execute("cap", config, period, str(cap), mode="account", cap=cap)
    table = pd.DataFrame(rows)
    dev = table[(table.stage == "cap") & (table.period == "develop")]
    eligible = dev[(dev.net_pnl > 0) & (dev.max_drawdown_pct <= 20)].copy()
    eligible["score"] = eligible.net_pnl / eligible.max_drawdown_pct.clip(lower=0.01)
    selected = (
        float(eligible.sort_values("score", ascending=False).iloc[0].label)
        if len(eligible)
        else 1.0
    )
    for bp in [2, 5]:
        execute(
            "cost",
            config,
            "full",
            f"plus{bp}bp",
            mode="account",
            cap=selected,
            fee=0.0005 + bp / 10000,
        )
    profile = {
        "exit_config": config.__dict__,
        "cap": selected,
        "fee_per_side": 0.0005,
        "size_increment": 0.001,
        "min_notional": 50,
        "starting_balance": 500,
        "development_gate_pass": bool(len(eligible)),
        "live_ready": False,
        "selection_uses": "2021-2023 only; all history previously viewed",
    }
    (ROOT / "candidate.json").write_text(json.dumps(profile, indent=2) + "\n")
    pd.DataFrame(rows).to_csv(ROOT / "followup_comparison.csv", index=False)
    paths = [
        Path(__file__),
        ROOT / "FOLLOWUP.md",
        ROOT.parents[2] / "strategy_python/btc_failed_breakout_20260916/strategy.py",
    ]
    (ROOT / "followup_provenance.json").write_text(
        json.dumps({str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}, indent=2)
        + "\n"
    )
    print("FOLLOWUP_COMPLETE", json.dumps(profile), flush=True)


if __name__ == "__main__":
    main()
