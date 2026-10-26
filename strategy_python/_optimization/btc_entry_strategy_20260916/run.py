"""Finite exit experiments followed by development-only sizing selection."""

import hashlib
import json
import os
from pathlib import Path

import pandas as pd

from strategy_python.btc_failed_breakout_20260916.strategy import CONFIGS, signals, simulate

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parents[2]
DATA = ROOT.parent / "mechanism_20260915"
PERIODS = {
    "develop": ("2021-01-01", "2024-01-01"),
    "validation": ("2024-01-01", "2025-01-01"),
    "review": ("2025-01-01", "2026-08-31"),
    "full": ("2021-01-01", "2026-08-31"),
}


def main():
    frozen = {
        str(p): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in [
            Path(__file__),
            ROOT / "PROTOCOL.md",
            PROJECT / "strategy_python/btc_failed_breakout_20260916/strategy.py",
            DATA / "BTC_minutes.parquet",
            DATA / "BTC_60m.parquet",
        ]
    }
    (ROOT / "provenance.json").write_text(json.dumps(frozen, indent=2) + "\n")
    minute = pd.read_parquet(DATA / "BTC_minutes.parquet")
    signal = signals(pd.read_parquet(DATA / "BTC_60m.parquet"))
    rows = []

    def execute(stage, config, period, label, **kwargs):
        start, end = [pd.Timestamp(x, tz="UTC") for x in PERIODS[period]]
        metrics, trades, daily = simulate(minute, signal, config, start, end, **kwargs)
        path = ROOT / "runs" / stage / label / period
        path.mkdir(parents=True, exist_ok=True)
        (path / "metrics.json").write_text(json.dumps(metrics, indent=2) + "\n")
        trades.to_csv(path / "trades.csv", index=False)
        daily.to_csv(path / "daily_equity.csv", header=["equity"])
        rows.append(
            {"stage": stage, "config": config.name, "label": label, "period": period, **metrics}
        )
        print(
            stage,
            label,
            period,
            round(metrics["net_pnl"], 3),
            round(metrics["max_drawdown_pct"], 3),
            flush=True,
        )
        return metrics

    for config in CONFIGS:
        for period in PERIODS:
            execute("exits", config, period, config.name)
    table = pd.DataFrame(rows)
    develop = table[table.period == "develop"]
    base = develop[develop.config == "time48"].iloc[0]
    allowed = develop[(develop.net_pnl > 0) & (develop.max_drawdown_pct <= base.max_drawdown_pct)]
    chosen = (
        allowed.sort_values("net_pnl", ascending=False).iloc[0].config if len(allowed) else "time48"
    )
    config = next(c for c in CONFIGS if c.name == chosen)
    sizes = {"cap0.5": {"cap": 0.5}, "cap1": {"cap": 1.0}}
    if config.stop != "none":
        sizes.update(
            {
                f"risk{risk}_cap{cap}": {"risk": risk, "cap": cap}
                for risk in [0.005, 0.01]
                for cap in [0.5, 1.0]
            }
        )
    for label, kwargs in sizes.items():
        for period in PERIODS:
            execute("sizing", config, period, label, mode="account", **kwargs)
    table = pd.DataFrame(rows)
    develop = table[(table.stage == "sizing") & (table.period == "develop")].copy()
    eligible = develop[(develop.net_pnl > 0) & (develop.max_drawdown_pct <= 20)].copy()
    eligible["score"] = eligible.net_pnl / eligible.max_drawdown_pct.clip(lower=0.01)
    selected = (
        eligible.sort_values("score", ascending=False).iloc[0].label if len(eligible) else "cap1"
    )
    for bp in [2, 5]:
        execute(
            "cost",
            config,
            "full",
            f"selected_plus{bp}bp",
            mode="account",
            fee=0.0005 + bp / 10000,
            **sizes[selected],
        )
    pd.DataFrame(rows).to_csv(ROOT / "comparison.csv", index=False)
    profile = {
        "exit_config": config.__dict__,
        "sizing": sizes[selected],
        "sizing_label": selected,
        "development_exit_pass": bool(len(allowed)),
        "development_sizing_pass": bool(len(eligible)),
        "selection_uses": "2021-2023 only; all history previously seen",
        "live_ready": False,
        "simulator": "custom minute OHLC, not Nautilus native",
    }
    (ROOT / "selection.json").write_text(json.dumps(profile, indent=2) + "\n")
    annual = []
    os.environ.setdefault("MPLCONFIGDIR", "/tmp/btc-entry-strategy-mpl")
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(2, 1, figsize=(12, 8), layout="constrained")
    paths = [
        ("exits", "time48", "fixed nominal / 48h"),
        ("exits", chosen, "selected exit / fixed nominal"),
        ("sizing", selected, "selected account"),
        ("cost", "selected_plus2bp", "account +2bp/side"),
        ("cost", "selected_plus5bp", "account +5bp/side"),
    ]
    for stage, label, title in paths:
        d = pd.read_csv(
            ROOT / "runs" / stage / label / "full/daily_equity.csv", index_col=0, parse_dates=True
        ).equity
        ax = axes[0 if stage == "exits" else 1]
        ax.plot(d.index, d - 500, label=title)
        prior = 500
        for year, g in d.groupby(d.index.year):
            closing = float(g.iloc[-1])
            annual.append(
                {"stage": stage, "label": label, "year": int(year), "pnl": closing - prior}
            )
            prior = closing
    for ax in axes:
        ax.axhline(0, color="gray", lw=0.5)
        ax.grid(alpha=0.2)
        ax.legend()
        ax.set_ylabel("PnL (USDT)")
    fig.savefig(ROOT / "curves.png", dpi=140)
    plt.close(fig)
    pd.DataFrame(annual).drop_duplicates().to_csv(ROOT / "annual.csv", index=False)
    print("COMPLETE", json.dumps(profile), flush=True)


if __name__ == "__main__":
    main()
