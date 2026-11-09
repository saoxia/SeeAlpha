"""Enforce that the business API can be installed and imported without Engine source."""

import ast
import shutil
import subprocess
import sys
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[2]


def test_business_api_never_imports_engine_or_nautilus_runtime():
    for path in sorted((PROJECT / "api").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module] if node.module else []
            else:
                continue
            for name in names:
                assert name.split(".", 1)[0] not in {"engine", "nautilus_trader"}, (
                    path.relative_to(PROJECT), name,
                )


def test_api_imports_and_builds_asgi_app_without_engine_tree(tmp_path):
    isolated = tmp_path / "platform"
    isolated.mkdir()
    for dirname in ("api", "core"):
        shutil.copytree(PROJECT / dirname, isolated / dirname, ignore=shutil.ignore_patterns("__pycache__"))

    script = """
import sys
from pathlib import Path
root = Path(sys.argv[1])
sys.path.insert(0, str(root))
from api.config import Settings
from api.main import create_app
app = create_app(Settings(root / "unused.db", database_url="sqlite:///" + str(root / "business.db")))
assert app.title
assert "engine" not in sys.modules
assert "nautilus_trader" not in sys.modules
assert Path(sys.modules["api"].__file__).is_relative_to(root)
"""
    done = subprocess.run(
        [sys.executable, "-I", "-c", script, str(isolated)],
        cwd=isolated, text=True, capture_output=True, timeout=45, check=False,
    )
    assert done.returncode == 0, done.stdout + done.stderr
