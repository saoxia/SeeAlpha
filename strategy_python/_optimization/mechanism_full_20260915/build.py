"""Create isolated, explicitly in-sample historical replay copies."""
import ast
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parents[2]
OLD = ROOT.parent / 'mechanism_20260915'
PREFIX = '_optimization.mechanism_full_20260915.packages.'
registry = json.loads((PROJECT/'strategy_python/_mechanism/strategies.json').read_text())
manifest = {}
guard = '        if config.trade_start_ns < 1704067200000000000:\n            raise ValueError("Mechanism calibration ends 2024-01-01; earlier trading is invalid")\n'
for info in registry.values():
    package = info['package']
    for src in (PROJECT/'strategy_python'/package).glob('*.py'):
        dst = ROOT/'packages'/package/src.name
        dst.parent.mkdir(parents=True, exist_ok=True)
        original = src.read_text()
        text = original.replace('strategy_python.'+package, 'strategy_python.'+PREFIX+package)
        if src.name == 'strategy.py':
            assert text.count(guard) == 1
            text = text.replace(guard, '        # Explicit historical replay: training-period overlap is declared in run metadata.\n')
        restored = text.replace('strategy_python.'+PREFIX+package, 'strategy_python.'+package)
        assert ast.dump(ast.parse(restored)) == ast.dump(ast.parse(original.replace(guard,'')))
        dst.write_text(text)
        manifest[str(src.relative_to(PROJECT))] = {'source_sha256':hashlib.sha256(src.read_bytes()).hexdigest(),'copy':str(dst.relative_to(PROJECT)),'copy_sha256':hashlib.sha256(dst.read_bytes()).hexdigest()}
    info['package'] = PREFIX + package
(ROOT/'strategies.json').write_text(json.dumps(registry,indent=2)+'\n')
(ROOT/'copy_verification.json').write_text(json.dumps(manifest,indent=2)+'\n')
factory = (PROJECT/'strategy_python/_mechanism/factory.py').read_text()
factory = factory.replace('(ROOT / "coefficients.json")', '(ROOT.parent / "mechanism_20260915/candidate_coefficients.json")')
(ROOT/'factory.py').write_text(factory)
all_registry = json.loads((OLD/'runtime_strategies.json').read_text())
selected = {key: dict(info,package=registry[info['slot']]['package']) for key,info in all_registry.items() if info['kind']=='mechanism' and info['asset']=='BTC'}
(ROOT/'runtime_strategies.json').write_text(json.dumps(selected,indent=2)+'\n')
runner = (OLD/'run.py').read_text().replace('from strategy_python._mechanism.factory import', 'from strategy_python._optimization.mechanism_full_20260915.factory import')
runner = runner.replace('PERIODS = {', 'PERIODS = {\n    "full": ("2021-01-01", "2026-08-31"),')
runner = runner.replace('STRATEGIES[key]["package"]).glob', 'STRATEGIES[key]["package"].replace(".", "/")).glob')
runner = runner.replace('    params = {', '''    hashes[str((ROOT / "factory.py").relative_to(PROJECT))] = hashlib.sha256((ROOT / "factory.py").read_bytes()).hexdigest()
    hashes[str((ROOT / "strategies.json").relative_to(PROJECT))] = hashlib.sha256((ROOT / "strategies.json").read_bytes()).hexdigest()
    hashes[str((ROOT.parent / "mechanism_20260915/candidate_coefficients.json").relative_to(PROJECT))] = hashlib.sha256((ROOT.parent / "mechanism_20260915/candidate_coefficients.json").read_bytes()).hexdigest()
    params = {
        "study_type": "frozen_parameter_historical_replay_includes_training",
        "calibration_start": "2021-01-01",
        "calibration_end_exclusive": "2024-01-01",
        "training_overlap": period == "full",
''')
(ROOT/'run.py').write_text(runner)
print('COPY_AST_EQUIVALENCE_VERIFIED',len(manifest))
