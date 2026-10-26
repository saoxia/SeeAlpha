"""Plot every declared development candidate; emphasize the frozen shortlist."""

import argparse
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
os.environ.setdefault("MPLCONFIGDIR", str(ROOT.parents[2] / ".local/cache/matplotlib"))
import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import pandas as pd


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--next", action="store_true", dest="next_round")
    args = parser.parse_args()
    prefix = "next" if args.next_round else "round1"
    baseline = "next_base" if args.next_round else "main"
    table = pd.read_csv(ROOT / (prefix + "_comparison.csv"))
    shortlist = json.loads((ROOT / (prefix + "_shortlist.json")).read_text())
    selected = {(r["slot"], r["variant"]) for r in shortlist}
    slots = table.slot.drop_duplicates().tolist()
    fig, axes = plt.subplots(len(slots), 2, figsize=(13, 3 * len(slots)), layout="constrained")
    for i, slot in enumerate(slots):
        group = table[table.slot == slot]
        variants = group.variant.tolist()
        variants.sort(key=lambda v: v == baseline or (slot, v) in selected)
        for v in variants:
            path = ROOT / "runs/develop" / (slot + ("__" + v if v != "main" else ""))
            d = pd.read_csv(path / "daily_equity.csv", index_col=0, parse_dates=True)
            equity = d.equity
            drawdown = (equity / equity.cummax().clip(lower=500) - 1) * 100
            style = {"color": "0.7", "alpha": 0.5, "lw": 0.8, "label": None, "zorder": 1}
            if v == baseline:
                style = {"color": "black", "lw": 1.6, "label": "baseline", "zorder": 3}
            elif (slot, v) in selected:
                style = {"lw": 1.4, "label": v, "zorder": 4}
            axes[i, 0].plot(equity.index, equity - 500, **style)
            axes[i, 1].plot(equity.index, drawdown, **style)
        axes[i, 0].set_title(slot + " | net PnL")
        axes[i, 1].set_title(slot + " | drawdown at daily closes")
        axes[i, 0].set_ylabel("USDT")
        axes[i, 1].set_ylabel("%")
        for ax in axes[i]:
            ax.axhline(0, color="0.5", lw=0.5)
            locator = mdates.AutoDateLocator(minticks=3, maxticks=5)
            ax.xaxis.set_major_locator(locator)
            ax.xaxis.set_major_formatter(mdates.ConciseDateFormatter(locator))
            ax.grid(alpha=0.2)
            ax.legend(fontsize=8, loc="best")
    fig.suptitle(
        "2021-2023 development replay (training overlap)\nBlack: baseline | colored: shortlisted | gray: other tested candidates"
    )
    fig.savefig(ROOT / (prefix + "_curves.png"), dpi=140)
    plt.close(fig)
    print(prefix.upper() + "_CURVES_COMPLETE")


if __name__ == "__main__":
    main()
