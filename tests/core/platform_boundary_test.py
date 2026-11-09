"""Architecture gate: business API must import without the Engine runtime."""

import ast
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DENIED = {"engine", "nautilus_trader"}


def test_business_modules_have_no_engine_or_nautilus_imports():
    for path in sorted((ROOT / "api").rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        parsed = ast.parse(source, filename=str(path))
        for node in ast.walk(parsed):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name.split(".", 1)[0] not in DENIED, (path, alias.name)
            elif isinstance(node, ast.ImportFrom) and node.module:
                assert node.module.split(".", 1)[0] not in DENIED, (path, node.module)
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id == "__import__" and node.args and isinstance(node.args[0], ast.Constant):
                    name = node.args[0].value
                    assert not isinstance(name, str) or name.split(".", 1)[0] not in DENIED, path


def test_api_bootstrap_does_not_import_engine_even_when_sources_are_present():
    code = """
import sys
class RejectEngineImports:
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".", 1)[0] in {"engine", "nautilus_trader"}:
            raise RuntimeError("Business API illegally imported " + fullname)
sys.meta_path.insert(0, RejectEngineImports())
from api.main import create_app
assert callable(create_app)
assert not any(name.split(".", 1)[0] in {"engine", "nautilus_trader"}
               for name in sys.modules)
"""
    run = subprocess.run(
        [sys.executable, "-c", code], cwd=ROOT, capture_output=True,
        text=True, timeout=30, check=False,
    )
    assert run.returncode == 0, run.stderr[-2000:]
