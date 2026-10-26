"""BTC consolidation preserves identity, market routing, and frozen migration rules."""

import ast
import hashlib
import importlib
import json
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import pytest
from nautilus_trader.model import BarType

from strategy_python.btc import STRATEGIES, create_strategy, spec_for

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("family", ["tfl", "tfs", "pbs"])
def test_duplicate_names_resolve_to_same_classes_and_submodules(family):
    base = f"strategy_python.btc_cycle_optimized_20260915_{family}"
    canonical = importlib.import_module(base + "_original")
    duplicate = importlib.import_module(base + "_optimized")
    for name in canonical.__all__:
        assert getattr(canonical, name) is getattr(duplicate, name)
    assert canonical.create_strategy is duplicate.create_strategy
    for module in ("config", "strategy", "execution", "state"):
        assert importlib.import_module(base + "_original." + module) is importlib.import_module(
            base + "_optimized." + module
        )


def test_removed_duplicate_sources_are_recoverable_byte_for_byte():
    evidence = json.loads(
        (
            ROOT / "strategy_python/_optimization/btc_consolidation_20260921/deduplication.json"
        ).read_text()
    )
    archive = ROOT / evidence["archive"]
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == evidence["archive_sha256"]
    with zipfile.ZipFile(archive) as files:
        for pair in evidence["pairs"]:
            for name, expected in pair["archived_sha256"].items():
                assert hashlib.sha256(files.read(name)).hexdigest() == expected


@pytest.mark.parametrize("key", STRATEGIES)
def test_four_active_slots_route_to_btc_and_correct_time(key):
    start = datetime(2022, 5, 1, tzinfo=UTC)
    strategy = create_strategy(key, start)
    assert str(strategy.config.instrument_id) == "BTCUSDT-PERP.BINANCE"
    assert strategy.config.bar_type == spec_for(key).bar_type
    assert strategy.config.trade_start_ns == int(start.timestamp() * 1e9)
    with pytest.raises(ValueError, match="BTC"):
        create_strategy(
            key, start, bar_type=BarType.from_str("ETHUSDT-PERP.BINANCE-1-MINUTE-LAST-EXTERNAL")
        )


def test_mean_reversion_short_migration_preserves_frozen_source_ast():
    target = ROOT / "strategy_python/btc_mean_reversion_short_5min"
    origin = ROOT / "strategy_python/mean_reversion_short_5min"
    manifest = json.loads((target / "migration.json").read_text())
    for name, expected_hash in manifest["source_sha256"].items():
        source = (origin / name).read_bytes()
        assert hashlib.sha256(source).hexdigest() == expected_hash
        expected = (
            source.decode()
            .replace("mean_reversion_short_5min", "btc_mean_reversion_short_5min")
            .replace("MeanReversionShort5Min", "BtcMeanReversionShort5Min")
            .replace("ETHUSDT", "BTCUSDT")
        )
        actual = (
            (target / name).read_text().replace("btcusdt-mean-reversion", "ethusdt-mean-reversion")
        )
        tree = ast.parse(actual)
        if name == "execution.py":

            class RemoveExchangeGuard(ast.NodeTransformer):
                def visit_ImportFrom(self, node):
                    return (
                        None
                        if node.module == "strategy_python._execution.entry_constraints"
                        else node
                    )

                def visit_Assign(self, node):
                    if any(
                        isinstance(t, ast.Name) and t.id == "checked_quantity" for t in node.targets
                    ):
                        return None
                    return node

                def visit_If(self, node):
                    if "opening_order_allowed" in ast.unparse(node.test):
                        return None
                    if any(
                        isinstance(n, ast.Assign)
                        and any(
                            isinstance(t, ast.Name) and t.id == "checked_quantity"
                            for t in n.targets
                        )
                        for n in node.body
                    ):
                        return None
                    return self.generic_visit(node)

            tree = RemoveExchangeGuard().visit(tree)
        assert ast.dump(ast.parse(expected)) == ast.dump(tree)
    strategy = create_strategy("mean_reversion_short", datetime(2022, 5, 1, tzinfo=UTC))
    source_module = importlib.import_module("strategy_python.mean_reversion_short_5min")
    source_config = source_module.MeanReversionShort5MinConfig(
        instrument_id=strategy.config.instrument_id,
        bar_type=strategy.config.bar_type,
        trade_start_ns=strategy.config.trade_start_ns,
    )
    assert json.loads(strategy.config.json()) == json.loads(source_config.json())
