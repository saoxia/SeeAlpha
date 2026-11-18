"""Offline regression tests for three shipping demo package definitions.

This test suite deliberately does not import Engine or NautilusTrader.
"""
import ast
import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2] / "strategy"


def _logic(folder):
    path = ROOT / folder / "logic.py"
    spec = importlib.util.spec_from_file_location(f"demo_{folder}_logic", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _carry_snapshot():
    now = 1_000_000_000_000_000_000
    return {
        "now_ns": now,
        "spot": {"price": "2000", "ts_ns": now - 10_000_000_000},
        "perpetual": {"price": "2002", "ts_ns": now - 10_000_000_000},
        "funding": {"rate": "0.0001", "ts_ns": now - 10_000_000_000},
        "positions": {"spot": "0", "perpetual": "0"},
    }


def _carry_params():
    return {
        "symbol": "ETHUSDT", "allocation": "0.1", "risk_limit": "0.1",
        "min_funding_rate": "0.00005", "max_basis_bps": "30",
        "max_leg_drift": "0.001", "max_age_seconds": 180,
    }


def test_funding_carry_equal_base_units_and_gross_rate():
    logic = _logic("demo_funding_carry")
    plan = logic.snapshot_plan(_carry_snapshot(), _carry_params())
    assert plan["action"] == "carry"
    assert [(leg["product"], leg["quantity"]) for leg in plan["targets"]] == [
        ("spot", "0.1"), ("futures_um", "-0.1"),
    ]
    assert plan["expected_gross_funding"] == "0.02002"


def test_funding_rate_basis_and_net_hedge_guards():
    logic = _logic("demo_funding_carry")
    sample = _carry_snapshot()
    sample["funding"]["rate"] = "-0.001"
    plan = logic.snapshot_plan(sample, _carry_params())
    assert plan["action"] == "flat"
    assert all(item["quantity"] == "0" for item in plan["targets"])
    sample = _carry_snapshot()
    sample["perpetual"]["price"] = "2020"
    assert logic.snapshot_plan(sample, _carry_params())["action"] == "flat"
    sample = _carry_snapshot()
    sample["positions"]["perpetual"] = "-0.02"
    assert logic.snapshot_plan(sample, _carry_params())["action"] == "blocked"
    assert logic.snapshot_plan(sample, _carry_params())["targets"] == []


def test_funding_carry_rejects_missing_stale_or_invalid_evidence():
    logic = _logic("demo_funding_carry")
    sample = _carry_snapshot()
    sample["funding"]["ts_ns"] -= 200_000_000_000
    with pytest.raises(logic.EvidenceError):
        logic.snapshot_plan(sample, _carry_params())
    sample = _carry_snapshot()
    del sample["positions"]
    with pytest.raises(logic.EvidenceError):
        logic.snapshot_plan(sample, _carry_params())
    sample = _carry_snapshot()
    sample["spot"]["price"] = "NaN"
    with pytest.raises(logic.EvidenceError):
        logic.snapshot_plan(sample, _carry_params())


def _option_params():
    return {
        "symbol": "ETHUSDT", "put_symbol": "ETH-261225-80-P",
        "call_symbol": "ETH-261225-130-C", "period": 5,
        "deviation": "1", "allocation": "0.1", "risk_limit": "0.1",
        "option_contracts": "0.1", "max_total_premium": "30",
        "min_dte_seconds": 86400, "max_age_seconds": 180,
    }


def _option_snapshot(close, now):
    expiry = now + 172_800_000_000_000
    return {
        "now_ns": now,
        "bar": {"close": str(close), "ts_ns": now - 1_000_000_000, "closed": True},
        "put": {
            "symbol": "ETH-261225-80-P", "ts_ns": now - 1_000_000_000,
            "expiry_ns": expiry, "ask": "2", "ask_size": "1",
            "strike": "80", "multiplier": "1",
        },
        "call": {
            "symbol": "ETH-261225-130-C", "ts_ns": now - 1_000_000_000,
            "expiry_ns": expiry, "ask": "2", "ask_size": "1",
            "strike": "130", "multiplier": "1",
        },
    }


def _warm_model(logic, params, *, final=90):
    model = logic.BollingerModel(params["period"], params["deviation"])
    now = 1_000_000_000_000_000_000
    for price in (100, 100, 100, 100):
        assert model.propose(_option_snapshot(price, now), params)["action"] == "warming_up"
        now += 60_000_000_000
    return model, _option_snapshot(final, now)


def test_option_wings_limit_perpetual_quantity_and_exit_at_mean():
    logic, params = _logic("demo_option_hedged_bollinger"), _option_params()
    model, snapshot = _warm_model(logic, params)
    plan = model.propose(snapshot, params)
    assert plan["action"] == "enter_long"
    assert [(row["product"], row["quantity"]) for row in plan["targets"]] == [
        ("options", "0.1"), ("options", "0.1"), ("futures_um", "0.1"),
    ]
    assert plan["premium_estimate"] == "0.4"
    assert model.position == 0  # proposer never commits an unaccepted basket
    model.commit(plan)
    later = _option_snapshot(102, snapshot["now_ns"] + 60_000_000_000)
    exit_plan = model.propose(later, params)
    assert exit_plan["action"] == "exit"
    assert all(leg["quantity"] == "0" for leg in exit_plan["targets"])
    model.commit(exit_plan)
    assert model.position == 0


def test_option_fails_closed_on_stale_quote_insufficient_hedge_and_cost():
    logic, params = _logic("demo_option_hedged_bollinger"), _option_params()
    model, sample = _warm_model(logic, params)
    sample["put"]["ts_ns"] = sample["now_ns"] - 181_000_000_000
    with pytest.raises(logic.EvidenceError):
        model.propose(sample, params)
    assert model.position == 0
    model, sample = _warm_model(logic, params)
    sample["put"]["multiplier"] = "0.5"
    with pytest.raises(logic.EvidenceError, match="保护"):
        model.propose(sample, params)
    model, sample = _warm_model(logic, params)
    sample["call"]["ask"] = "1000"
    with pytest.raises(logic.EvidenceError, match="权利金"):
        model.propose(sample, params)


def test_option_rejects_bars_from_future_and_mismatched_expiry():
    logic, params = _logic("demo_option_hedged_bollinger"), _option_params()
    model, sample = _warm_model(logic, params)
    sample["call"]["expiry_ns"] -= 1_000_000_000
    assert model.propose(sample, params)["action"] == "exit"
    model, sample = _warm_model(logic, params)
    sample["bar"]["closed"] = False
    with pytest.raises(logic.EvidenceError):
        model.propose(sample, params)


def test_all_public_sample_packages_are_labeled_demo_and_declare_entry_points():
    folders = sorted(x for x in ROOT.iterdir() if x.is_dir())
    assert [folder.name for folder in folders] == [
        "demo_ema_crossover", "demo_funding_carry", "demo_option_hedged_bollinger",
    ]
    for folder in folders:
        manifest = json.loads((folder / "seealpha-package.json").read_text(encoding="utf-8"))
        assert manifest["name"] == folder.name
        assert manifest["name"].startswith("demo_")
        assert manifest["format"] == 1
        source = (folder / "strategy.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        module, cls = manifest["entry_point"].split(":")
        assert module == "strategy"
        assert any(isinstance(node, ast.ClassDef) and node.name == cls for node in tree.body)
        assert any(isinstance(node, ast.FunctionDef) and node.name == "prepare" for node in tree.body)
        assert "submit_order(" not in source
        assert "close_all_positions(" not in source
