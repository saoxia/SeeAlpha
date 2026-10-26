"""ETH/BTC factories backed by frozen pre-2024 conditional-path coefficients."""

import importlib
import json
from datetime import UTC, datetime
from pathlib import Path

from nautilus_trader.model.data import BarType
from nautilus_trader.model.identifiers import InstrumentId

ROOT = Path(__file__).resolve().parent
STRATEGIES = json.loads((ROOT / "strategies.json").read_text())
COEFFICIENTS = json.loads((ROOT.parent / "mechanism_20260915/candidate_coefficients.json").read_text())["coefficients"]


def create_strategy(asset, key, start=None, *, variant="main", **overrides):
    if asset not in COEFFICIENTS or key not in STRATEGIES:
        raise ValueError("Unknown mechanism asset or strategy")
    start = start or datetime(2024, 1, 1, tzinfo=UTC)
    if start.tzinfo is None:
        raise ValueError("Explicit timezone required")
    info = STRATEGIES[key]
    key = info["mechanism_key"]
    coefficient_key = (
        "MRL_2.0" if key == "MRL_original" else "MRL_2.25" if key == "MRL_optimized" else key
    )
    coefficient = COEFFICIENTS[asset][coefficient_key]
    if key.startswith("MRL"):
        if variant not in ("main", "flat_risk"):
            raise ValueError("Unknown MRL variant")
        params = {
            "mechanism_add_multipliers": tuple(coefficient["add_band_multipliers"]),
            "mechanism_flat_risk": variant == "flat_risk",
            "mechanism_flat_multiplier": coefficient["flat_risk_multiplier"],
        }
    elif key in ("TFL", "TFS"):
        if variant not in ("main", "stop70", "stop90", "activation40", "activation60"):
            raise ValueError("Unknown trend variant")
        params = {
            "mechanism_stop_atr": coefficient["stop_atr"],
            "mechanism_activation_atr": coefficient["activation_atr"],
        }
        if variant.startswith("stop"):
            params["mechanism_stop_atr"] = coefficient["stop_neighbors"][
                str(int(variant[4:]) / 100)
            ]
        elif variant.startswith("activation"):
            params["mechanism_activation_atr"] = coefficient["activation_neighbors"][
                str(int(variant[10:]) / 100)
            ]
    else:
        if variant not in ("main", "buffer70", "buffer90"):
            raise ValueError("Unknown PBS variant")
        buffer = (
            coefficient["stop_atr_buffer"]
            if variant == "main"
            else coefficient["buffer_neighbors"][str(int(variant[6:]) / 100)]
        )
        # Range endpoints move by the buffer difference: same training structures.
        delta = buffer - coefficient["stop_atr_buffer"]
        params = {
            "mechanism_buffer_atr": buffer,
            "mechanism_min_stop_atr": coefficient["min_stop_atr"] + delta,
            "mechanism_max_stop_atr": coefficient["max_stop_atr"] + delta,
            "risk_fraction": 0.015,
            "max_notional_fraction": 3.0,
        }
    params.update(overrides)
    module = importlib.import_module("strategy_python." + info["package"])
    config = getattr(module, info["prefix"] + "Config")(
        instrument_id=InstrumentId.from_str(f"{asset}USDT-PERP.BINANCE"),
        bar_type=BarType.from_str(f"{asset}USDT-PERP.BINANCE-1-MINUTE-LAST-EXTERNAL"),
        trade_start_ns=int(start.timestamp() * 1e9),
        **params,
    )
    return getattr(module, info["prefix"] + "Strategy")(config)
