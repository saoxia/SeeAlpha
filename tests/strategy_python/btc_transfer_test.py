"""BTC transfer preserves source logic and uses BTC runtime configuration."""

import ast
import importlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from strategy_python._research.btc_transfer_20260914.run import (
    STRATEGIES,
    create_strategy,
    spec_for,
)

ROOT = Path(__file__).resolve().parents[2]


def normalized_ast(text):
    tree = ast.parse(text)
    for node in ast.walk(tree):
        if (
            isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
            and node.body
            and isinstance(node.body[0], ast.Expr)
            and isinstance(node.body[0].value, ast.Constant)
            and isinstance(node.body[0].value.value, str)
        ):
            node.body.pop(0)
    return ast.dump(tree, include_attributes=False)


@pytest.mark.parametrize("key", STRATEGIES)
def test_btc_domain_logic_matches_source_after_identifier_mapping(key):
    info = STRATEGIES[key]
    directory = ROOT / "strategy_python"
    for source in (directory / info["source"]).glob("*.py"):
        if source.name == "backtest.py":
            continue
        expected = source.read_text().replace(info["source"], info["package"])
        expected = expected.replace(info["source_prefix"], info["prefix"])
        expected = expected.replace("ETHUSDT", "BTCUSDT")
        actual = (directory / info["package"] / source.name).read_text()
        assert normalized_ast(actual) == normalized_ast(expected), source.name


@pytest.mark.parametrize("key", STRATEGIES)
def test_btc_defaults_and_standalone_factory_match_migrated_source(key):
    info = STRATEGIES[key]
    start = datetime(2025, 1, 1, tzinfo=UTC)
    actual = create_strategy(key, start)
    spec = spec_for(key)
    source = importlib.import_module("strategy_python." + info["source"])
    expected = getattr(source, info["source_prefix"] + "Config")(
        instrument_id=spec.instrument_id,
        bar_type=spec.bar_type,
        trade_start_ns=int(start.timestamp() * 1_000_000_000),
    )
    assert json.loads(actual.config.json()) == json.loads(expected.json())
    assert str(actual.config.instrument_id) == "BTCUSDT-PERP.BINANCE"
    entry = importlib.import_module("strategy_python." + info["package"] + ".backtest")
    assert entry.BACKTEST_SPEC == spec
    standalone = entry.create_strategy(start)
    assert type(standalone) is type(actual)
    assert standalone.config == actual.config


def test_pbs_equal_risk_comparison_is_explicit_and_does_not_change_defaults():
    start = datetime(2025, 1, 1, tzinfo=UTC)
    original = create_strategy("PBS_original", start)
    optimized = create_strategy("PBS_optimized", start)
    matched = create_strategy(
        "PBS_original", start, {"risk_fraction": 0.015, "max_notional_fraction": 3.0}
    )
    assert original.config.risk_fraction == 0.005
    assert original.config.max_notional_fraction == 1
    assert matched.config.risk_fraction == optimized.config.risk_fraction == 0.015
    assert json.loads(matched.config.json()) == json.loads(optimized.config.json())
