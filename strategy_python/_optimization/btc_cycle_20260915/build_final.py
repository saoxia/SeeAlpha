"""Materialize eight selected full-replay configurations into new independent packages."""

import ast
import hashlib
import importlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parents[2]


def tuples(value):
    return tuple(tuples(x) for x in value) if isinstance(value, list) else value


def configure(source, config):
    data = source.encode()
    lines = data.splitlines(keepends=True)
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))
    replacements = []
    for cls in ast.parse(source).body:
        if not isinstance(cls, ast.ClassDef):
            continue
        for node in cls.body:
            if not isinstance(node, ast.AnnAssign) or not isinstance(node.target, ast.Name):
                continue
            key = node.target.id
            if node.value is None or key == "trade_start_ns":
                continue
            assert key in config, key
            old = ast.literal_eval(node.value)
            new = tuples(config[key]) if isinstance(old, tuple) else config[key]
            if new == old:
                continue
            start = offsets[node.value.lineno - 1] + node.value.col_offset
            end = offsets[node.value.end_lineno - 1] + node.value.end_col_offset
            replacements.append((start, end, repr(new).encode()))
    for start, end, replacement in sorted(replacements, reverse=True):
        data = data[:start] + replacement + data[end:]
    return data.decode()


def main():
    selected = json.loads((ROOT / "final_selection.json").read_text())
    slots = json.loads((ROOT / "slots.json").read_text())
    assert len(selected) == 8 and {r["slot"] for r in selected} == set(slots)
    assert (ROOT / "full_cycle_comparison.csv").exists(), "Complete 18-case report first"
    registry = json.loads((ROOT / "next_strategies.json").read_text())
    frozen = json.loads((ROOT / "frozen_full_cycle.json").read_text())
    for path, digest in frozen.items():
        assert hashlib.sha256((PROJECT / path).read_bytes()).hexdigest() == digest, path
    final = []
    for item in selected:
        slot, source_slot, variant = item["slot"], item["source_slot"], item["variant"]
        assert slot.split("_")[0] == source_slot.split("_")[0]
        if slot.startswith("MRL"):
            assert slot == source_slot
        run = ROOT / "runs/full" / (source_slot + "__" + variant)
        params = json.loads((run / "parameters.json").read_text())
        metrics = json.loads((run / "metrics.json").read_text())
        baseline_path = ROOT / "runs/full" / (source_slot + "__next_base")
        baseline = json.loads((baseline_path / "metrics.json").read_text())
        assert (
            metrics["net_pnl"] > baseline["net_pnl"]
            and metrics["max_drawdown_pct"] <= baseline["max_drawdown_pct"]
        ), "Selected configuration has not improved full return/drawdown"
        assert (
            params["runner_sha256"]
            == frozen[str((ROOT / (item["module"] + ".py")).relative_to(PROJECT))]
        )
        for source, digest in params["source_sha256"].items():
            assert hashlib.sha256((PROJECT / source).read_bytes()).hexdigest() == digest, source
        info = registry[source_slot]
        old_package = "strategy_python." + info["package"]
        name = "btc_cycle_optimized_20260915_" + slot.lower()
        new_package = "strategy_python." + name
        dest = PROJECT / "strategy_python" / name
        assert not dest.exists(), f"Refuse to overwrite {dest}"
        dest.mkdir()
        source_dir = PROJECT / old_package.replace(".", "/")
        for path in source_dir.glob("*.py"):
            code = path.read_text().replace(old_package, new_package)
            if path.name == "config.py":
                code = configure(code, params["config"])
            (dest / path.name).write_text(code)
        prefix = info["prefix"]
        preset = f'''"""Frozen research preset. No order submission occurs on import."""
from datetime import UTC, datetime
from nautilus_trader.model.data import BarType
from nautilus_trader.model.identifiers import InstrumentId
from .config import {prefix}Config
from .strategy import {prefix}Strategy

def create_config(start=None):
    start = start or datetime(2021, 1, 1, tzinfo=UTC)
    if start.tzinfo is None:
        raise ValueError("Explicit timezone required")
    return {prefix}Config(instrument_id=InstrumentId.from_str("BTCUSDT-PERP.BINANCE"), bar_type=BarType.from_str("BTCUSDT-PERP.BINANCE-1-MINUTE-LAST-EXTERNAL"), trade_start_ns=int(start.timestamp()*1e9))

def create_strategy(start=None):
    return {prefix}Strategy(create_config(start))
'''
        (dest / "preset.py").write_text(preset)
        with (dest / "__init__.py").open("a") as f:
            f.write("\nfrom .preset import create_config, create_strategy\n")
        baseline_config = json.loads((baseline_path / "parameters.json").read_text())["config"]
        changes = {
            k: {"before": baseline_config[k], "after": v}
            for k, v in params["config"].items()
            if baseline_config.get(k) != v
        }
        profile = {
            **item,
            "package": new_package,
            "validated_research_run": str(run.relative_to(PROJECT)),
            "config": params["config"],
            "parameter_changes": changes,
            "research_only": True,
        }
        (dest / "profile.json").write_text(json.dumps(profile, indent=2) + "\n")
        (dest / "README.md").write_text(
            f"# {slot} 独立新版本\n\n来源：{variant}。所有策略参数已写入本包Config默认值；create_config/create_strategy使用BTC分钟行情及明确起始时间。\n\n选择理由：{item['reason']}\n\n完整配置、参数变化和研究回放来源见profile.json。历史研究含训练重叠和选择偏差，不代表未来盈利或实盘批准。\n"
        )
        module = importlib.import_module(new_package)
        assert json.loads(module.create_config().json()) == params["config"], slot
        final.append(profile)
    (ROOT / "FINAL_STRATEGIES.json").write_text(json.dumps(final, indent=2) + "\n")
    print("FINAL_PACKAGES_MATERIALIZED", len(final), "NATIVE_VERIFICATION_PENDING")


if __name__ == "__main__":
    main()
