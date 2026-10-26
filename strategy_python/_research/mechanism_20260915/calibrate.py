"""Fit the declared conditional-path candidate; no strategy-PnL optimization."""

import hashlib
import json

import numpy as np
import pandas as pd

from strategy_python._research.mechanism_20260915.data import ROOT, load_minutes


def enrich(asset):
    frame = pd.read_parquet(ROOT / f"{asset}_paths.parquet")
    frame = frame[
        frame.eligible
        & (
            ((frame.family == "MRL") & (frame.hours == 48))
            | ((frame.family != "MRL") & (frame.hours == 24))
        )
    ].copy()
    minute = load_minutes(asset)
    ns = minute.index.as_unit("ns").asi8
    high, low = minute.high.to_numpy(), minute.low.to_numpy()
    five = pd.read_parquet(ROOT / f"{asset}_5m.parquet")
    mids = pd.Series(five.close.rolling(20).mean().to_numpy(), index=five.index.as_unit("ns").asi8)
    records = []
    for event in frame.to_dict("records"):
        start = ns.searchsorted(event["entry_ns"], side="right")
        end = start + event["hours"] * 60
        price, side = event["price"], event["direction"]
        favorable = high[start:end] / price - 1 if side == 1 else 1 - low[start:end] / price
        adverse = 1 - low[start:end] / price if side == 1 else high[start:end] / price - 1
        scale = event["atr_fraction"]
        target = scale
        if event["family"] == "MRL":
            mid = float(mids.loc[event["signal_ns"]])
            scale = mid / event["quote_price"] - 1
            target = mid / price - 1
        if not np.isfinite(scale) or scale <= 0:
            continue
        hits = np.flatnonzero(favorable >= target)
        before = max(0.0, float(adverse[: int(hits[0]) + 1].max())) if len(hits) else np.nan
        records.append(
            {
                **event,
                "reference_scale": scale,
                "reference_target": target,
                "recovered": bool(len(hits)),
                "noise_before_recovery": before,
                "noise_ratio": before / scale,
                "mfe_ratio": event["mfe"] / scale,
                "overshoot_atr": max(
                    0.0, (before - event.get("structure_fraction", np.nan)) / scale
                )
                if event["family"] == "PBS" and len(hits)
                else np.nan,
            }
        )
    result = pd.DataFrame(records)
    result.to_parquet(ROOT / f"{asset}_mechanism_paths.parquet", index=False)
    return result


def estimate(data):
    coefficients = {}
    for asset, asset_data in data.groupby("asset"):
        output = {}
        trends = asset_data[asset_data.family.isin(["TFL", "TFS"])]
        for family in ("TFL", "TFS"):
            group = trends[trends.family == family]
            recovered = group[group.recovered]
            stop_pool = recovered if len(recovered) >= 100 else trends[trends.recovered]
            activation_pool = group if len(group) >= 100 else trends
            output[family] = {
                "stop_atr": float(stop_pool.noise_ratio.quantile(0.8)),
                "activation_atr": float(activation_pool.mfe_ratio.median()),
                "stop_pool": "direction" if len(recovered) >= 100 else "both_directions_same_asset",
                "activation_pool": "direction"
                if len(group) >= 100
                else "both_directions_same_asset",
                "direction_n": len(group),
                "recovered_n": len(recovered),
                "stop_pool_n": len(stop_pool),
                "activation_pool_n": len(activation_pool),
                "stop_neighbors": {
                    str(q): float(stop_pool.noise_ratio.quantile(q)) for q in (0.7, 0.9)
                },
                "activation_neighbors": {
                    str(q): float(activation_pool.mfe_ratio.quantile(q)) for q in (0.4, 0.6)
                },
            }
        for variant in ("2.0", "2.25"):
            group = asset_data[(asset_data.family == "MRL") & (asset_data.variant == variant)]
            recovered = group[group.recovered]
            output["MRL_" + variant] = {
                "add_band_multipliers": [
                    float(recovered.noise_ratio.quantile(q)) for q in (0.25, 0.5, 0.75)
                ],
                "n": len(group),
                "recovered_n": len(recovered),
                "recovery_probability": len(recovered) / len(group),
                "flat_risk_multiplier": float(
                    (group.risk_strength_coefficient * group.risk_boundary_coefficient).mean()
                ),
            }
        pbs = asset_data[asset_data.family == "PBS"]
        recovered = pbs[pbs.recovered]
        buffer = float(recovered.overshoot_atr.quantile(0.8))
        distances = pbs.structure_fraction / pbs.atr_fraction + buffer
        output["PBS"] = {
            "stop_atr_buffer": buffer,
            "min_stop_atr": float(distances.quantile(0.1)),
            "max_stop_atr": float(distances.quantile(0.9)),
            "n": len(pbs),
            "recovered_n": len(recovered),
            "sample_warning": len(recovered) < 100,
            "buffer_neighbors": {
                str(q): float(recovered.overshoot_atr.quantile(q)) for q in (0.7, 0.9)
            },
        }
        coefficients[asset] = output
    return coefficients


def main():
    data = pd.concat([enrich(asset) for asset in ("ETH", "BTC")], ignore_index=True)
    training = data[data.split == "train"]
    assert training.label_end_ns.max() <= pd.Timestamp("2024-01-01", tz="UTC").value
    frozen = {
        "definition_sha256": hashlib.sha256(
            (ROOT / "CANDIDATE_DEFINITION.md").read_bytes()
        ).hexdigest(),
        "calibration_end_exclusive": "2024-01-01",
        "first_allowed_trade": "2024-01-01",
        "coefficients": estimate(training),
        "training_rows": len(training),
        "input_sha256": {
            str(ROOT / f"{asset}_mechanism_paths.parquet"): hashlib.sha256(
                (ROOT / f"{asset}_mechanism_paths.parquet").read_bytes()
            ).hexdigest()
            for asset in ("ETH", "BTC")
        },
    }
    (ROOT / "candidate_coefficients.json").write_text(
        json.dumps(frozen, indent=2, allow_nan=False) + "\n"
    )
    annual = {str(year): estimate(training[training.year == year]) for year in (2021, 2022, 2023)}
    (ROOT / "annual_coefficients.json").write_text(
        json.dumps(annual, indent=2, allow_nan=False) + "\n"
    )
    print(json.dumps(frozen["coefficients"], indent=2), flush=True)


if __name__ == "__main__":
    main()
