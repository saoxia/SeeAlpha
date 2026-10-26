"""Delivered defaults, isolated modules and portfolio assembly agree with frozen trials."""

import ast
import importlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from strategy_python import portfolio
from strategy_python import portfolio_optimized_backtest as optimized
from strategy_python._research.four_strategy_20260914.materialize import PACKAGES
from strategy_python._research.four_strategy_20260914.research import BASE, CANDIDATES, NAMES

ROOT = Path(__file__).resolve().parents[3]
RESEARCH = ROOT / "strategy_python/_optimization/four_strategy_20260914"


@pytest.mark.parametrize("key", NAMES)
def test_delivered_defaults_match_selected_trial(key):
    selected = json.loads((RESEARCH / f"{key}_selection.json").read_text())["selected"]
    module = importlib.import_module("strategy_python." + PACKAGES[key] + ".backtest")
    start = datetime(2024, 1, 1, tzinfo=UTC)
    actual = module.create_strategy(start).config
    original_class, _ = portfolio.STRATEGY_REGISTRY[NAMES[key]]
    expected = original_class(
        instrument_id=module.INSTRUMENT_ID,
        bar_type=module.BAR_TYPE,
        trade_start_ns=int(start.timestamp() * 1e9),
        **{**BASE[key], **CANDIDATES[key][selected]},
    )
    assert json.loads(actual.json()) == json.loads(expected.json())
    assert type(actual).__module__ == "strategy_python." + PACKAGES[key] + ".config"


@pytest.mark.parametrize("name", PACKAGES.values())
def test_new_domain_modules_do_not_import_original_strategies(name):
    for file in (ROOT / "strategy_python" / name).glob("*.py"):
        for node in ast.walk(ast.parse(file.read_text())):
            if isinstance(node, ast.ImportFrom):
                assert not any(
                    (node.module or "").startswith("strategy_python." + old)
                    for old in NAMES.values()
                )


def test_new_portfolio_preserves_original_registry(monkeypatch):
    original = dict(portfolio.STRATEGY_REGISTRY)
    registry = dict(original)
    monkeypatch.setattr(portfolio, "STRATEGY_REGISTRY", registry)
    monkeypatch.setattr(optimized, "STRATEGY_REGISTRY", registry)
    optimized.register_optimized_strategies()
    assert all(registry[k] == v for k, v in original.items())
    config = portfolio.load_portfolio(optimized.DEFAULT_CONFIG)
    strategies, budget = portfolio.create_strategies(datetime(2024, 1, 1, tzinfo=UTC), config)
    assert len(strategies) == 4 and budget is None
    assert len({str(s.strategy_id) for s in strategies}) == 4
    assert {type(s).__module__.split(".")[1] for s in strategies} == set(PACKAGES.values())
    assert strategies[3].config.risk_fraction == 0.015
    assert strategies[3].config.max_notional_fraction == 3.0


def test_mean_reversion_deadline_stops_adding_without_claiming_time_exit():
    from decimal import Decimal

    module = importlib.import_module("strategy_python." + PACKAGES["MRL"] + ".backtest")
    strategy_class = type(module.create_strategy())

    class Probe(strategy_class):
        def _cancel_entry(self, state):
            self.entry_cancelled = True

        def _submit_emergency_exit(self, state, reason):
            self.exit_requested = True

    strategy = Probe(module.create_strategy().config)
    state = strategy.trade_state
    state.position_start_ns = 1
    state.quantity = Decimal(1)
    deadline = 1 + strategy.config.max_holding_hours * 3600_000_000_000
    strategy._refresh_add_order(state, deadline)
    assert strategy.entry_cancelled
    assert not getattr(strategy, "exit_requested", False)
    assert state.quantity == Decimal(1)


@pytest.mark.parametrize("key", NAMES)
def test_research_environment_matches_original_backtest(key):
    from strategy_python._research.four_strategy_20260914.environment import backtest_spec

    original = importlib.import_module("strategy_python." + NAMES[key] + ".backtest")
    assert backtest_spec(key) == original.BACKTEST_SPEC
