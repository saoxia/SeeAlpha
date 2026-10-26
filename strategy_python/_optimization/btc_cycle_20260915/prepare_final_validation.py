"""Create an isolated native runner for materialized final packages."""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main():
    profiles = json.loads((ROOT / "FINAL_STRATEGIES.json").read_text())
    assert len(profiles) == 8
    original = json.loads((ROOT / "next_runtime_strategies.json").read_text())
    factories = json.loads((ROOT / "next_strategies.json").read_text())
    runtime, registry, experiments = {}, {}, {}
    for p in profiles:
        slot = p["slot"]
        package = p["package"].removeprefix("strategy_python.")
        runtime[slot] = {**original[p["source_slot"]], "slot": slot, "package": package}
        registry[slot] = {**factories[p["source_slot"]], "package": package}
        experiments[slot] = {"main": {"overrides": {}}}
    for name, value in [
        ("final_runtime_strategies.json", runtime),
        ("final_strategies.json", registry),
        ("final_experiments.json", experiments),
    ]:
        target = ROOT / name
        assert not target.exists(), target
        target.write_text(json.dumps(value, indent=2) + "\n")
    factory = '''"""Use only the defaults of each independently materialized package."""
import importlib
import json
from pathlib import Path
ROOT=Path(__file__).resolve().parent
STRATEGIES=json.loads((ROOT/'final_strategies.json').read_text())
def create_strategy(asset,key,start=None,*,variant='main',**overrides):
    if asset!='BTC' or variant!='main' or overrides:
        raise ValueError('Final verification must use the frozen BTC preset')
    return importlib.import_module('strategy_python.'+STRATEGIES[key]['package']).create_strategy(start)
'''
    target = ROOT / "final_factory.py"
    assert not target.exists()
    target.write_text(factory)
    runner = (ROOT / "next_run.py").read_text()
    for old, new in [
        ("next_runtime_strategies.json", "final_runtime_strategies.json"),
        ("next_experiments.json", "final_experiments.json"),
        ("next_factory", "final_factory"),
        ("next_strategies.json", "final_strategies.json"),
    ]:
        runner = runner.replace(old, new)
    old = 'output = ROOT / "runs" / period'
    assert old in runner
    runner = runner.replace(old, 'output = ROOT / "final_runs" / period')
    target = ROOT / "final_run.py"
    assert not target.exists()
    target.write_text(runner)
    print("FINAL_NATIVE_RUNNER_PREPARED")


if __name__ == "__main__":
    main()
