"""Behavioral invariants for the frozen BTC price scale."""

import hashlib
import importlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from strategy_python._research.btc_scale_20260915.build import FIELDS
from strategy_python._research.btc_scale_20260915.run import STRATEGIES, create_strategy
from strategy_python._research.btc_transfer_20260914.run import create_strategy as baseline

ROOT = Path(__file__).resolve().parents[2]
CAL = ROOT / "strategy_python/_optimization/btc_scale_20260915/calibration.json"
FACTOR = json.loads(CAL.read_text())["factor"]
START = datetime(2022, 5, 1, tzinfo=UTC)


@pytest.mark.parametrize("key", STRATEGIES)
def test_only_price_scale_defaults_change_and_factories_agree(key):
    info = STRATEGIES[key]
    original = json.loads(baseline(key, START).config.json())
    scaled = create_strategy(key, START)
    actual = json.loads(scaled.config.json())
    for name, value in original.items():
        if name in FIELDS[info["family"]] and value is not None:
            assert actual[name] == pytest.approx(value * FACTOR)
        else:
            assert actual[name] == value, name
    entry = importlib.import_module(f"strategy_python.{info['package']}.backtest")
    assert entry.create_strategy(START).config == scaled.config
    assert entry.create_strategy().config.trade_start_ns == 1609459200000000000
    with pytest.raises(ValueError, match="2020"):
        create_strategy(key, datetime(2020, 12, 31, tzinfo=UTC))
    scale = importlib.import_module(f"strategy_python.{info['package']}.scale")
    assert scale.CALIBRATION_SHA256 == hashlib.sha256(CAL.read_bytes()).hexdigest()


@pytest.mark.parametrize("key", [k for k in STRATEGIES if k.startswith("MRL")])
def test_add_order_distances_and_risk_projection_share_scaled_parameters(key):
    old = baseline(key, START).risk_model.parameters
    strategy = create_strategy(key, START)
    new = strategy.risk_model.parameters
    assert new.stop_distance == pytest.approx(old.stop_distance * FACTOR)
    assert new.stop_distance == strategy.config.hard_stop_distance
    assert dict(new.add_offsets) == pytest.approx({k: v * FACTOR for k, v in old.add_offsets})
    assert new.entry_fee_rate == old.entry_fee_rate
    assert new.forced_exit_fee_rate == old.forced_exit_fee_rate
    assert new.risk_fraction == old.risk_fraction
    assert new.strength_knots == old.strength_knots


@pytest.mark.parametrize("key", [k for k in STRATEGIES if k.startswith(("TFL", "TFS"))])
def test_dynamic_lock_preserves_curve_at_equal_normalized_returns(key):
    package = STRATEGIES[key]["package"]
    old = importlib.import_module(
        f"strategy_python.{package.replace('btc_scaled_', 'btc_')}.profit_protection"
    )
    new = importlib.import_module(f"strategy_python.{package}.profit_protection")
    for value in (0, 0.01, 0.0155, 0.02, 0.03, 0.035, 0.1):
        assert new.peak_profit_lock_ratio(value * FACTOR) == pytest.approx(
            old.peak_profit_lock_ratio(value)
        )
    with pytest.raises(ValueError):
        new.peak_profit_lock_ratio(float("nan"))


def test_current_strategy_source_baselines_are_unchanged():
    manifest = ROOT / "handover/current_source_baselines.json"
    for path, digest in json.loads(manifest.read_text())["source_sha256"].items():
        assert hashlib.sha256((ROOT / path).read_bytes()).hexdigest() == digest
