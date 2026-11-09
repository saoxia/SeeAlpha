"""Platform-only environment: API works with zero Engine/Nautilus distribution.

Run with: platform/.venv/bin/python -m pytest -c platform/pyproject.toml platform/tests
"""

import importlib.metadata
import shutil
import subprocess
import sys
from pathlib import Path

PLATFORM_ROOT = Path(__file__).resolve().parents[2]


def test_platform_environment_excludes_engine_runtime():
    try:
        importlib.metadata.distribution("nautilus-trader")
    except importlib.metadata.PackageNotFoundError:
        pass
    else:
        raise AssertionError("Platform runtime must not install nautilus-trader")


def test_platform_imports_without_engine_source(tmp_path):
    root = tmp_path / "platform-source-only"
    root.mkdir()
    for name in ("api", "core"):
        shutil.copytree(
            PLATFORM_ROOT / name, root / name,
            ignore=shutil.ignore_patterns("__pycache__"),
        )
    assert not (root / "engine").exists()

    script = """
import sys
from pathlib import Path
root = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(root))
from api.config import Settings
from api.main import create_app
app = create_app(Settings(root / "unused.db", database_url="sqlite:///" + str(root / "business.db")))
assert app.title
assert Path(sys.modules["api"].__file__).resolve().is_relative_to(root)
assert Path(sys.modules["core"].__file__).resolve().is_relative_to(root)
assert "engine" not in sys.modules
assert "nautilus_trader" not in sys.modules
"""
    result = subprocess.run(
        [sys.executable, "-I", "-c", script, str(root)],
        cwd=root, text=True, capture_output=True, timeout=45, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
