"""Platform-only environment: API works with zero Engine/Nautilus distribution.

Run with: platform/.venv/bin/python -m pytest -c platform/pyproject.toml platform/tests
"""

import ast
import importlib.metadata
import re
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


def test_business_api_never_imports_engine_or_nautilus_runtime():
    for path in sorted((PLATFORM_ROOT / "api").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module] if node.module else []
            elif (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "__import__"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            ):
                # Catch literal dynamic imports without duplicating the ASGI isolation test.
                names = [node.args[0].value]
            else:
                continue
            for name in names:
                assert name.split(".", 1)[0] not in {"engine", "nautilus_trader"}, (
                    path.relative_to(PLATFORM_ROOT), name,
                )


def test_platform_tests_do_not_import_legacy_engine_or_test_helpers():
    """Keep the standalone suite independent of old in-process test fixtures."""
    forbidden = {"engine", "nautilus_trader", "nautilus_support"}
    for path in sorted((PLATFORM_ROOT / "platform/tests").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module] if node.module else []
            else:
                continue
            for name in names:
                assert name.split(".", 1)[0] not in forbidden | {"tests"}, (
                    path.relative_to(PLATFORM_ROOT), name,
                )


def test_platform_tests_cannot_opt_into_legacy_engine_http_fixture():
    """No standalone test may silently reattach to the old ASGI Engine test fixture."""
    for path in sorted((PLATFORM_ROOT / "platform/tests").glob("*_test.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.arg):
                assert node.arg != "engine_http", (path, node.lineno)
            elif isinstance(node, ast.Name):
                assert node.id != "engine_http", (path, node.lineno)
            elif (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "usefixtures"
            ):
                assert not any(
                    isinstance(arg, ast.Constant) and arg.value == "engine_http"
                    for arg in node.args
                ), (path, node.lineno)


def test_platform_does_not_regain_engine_worker_or_venue_ownership():
    """Keep the Platform-only half of the former Engine phase1 boundary test."""
    root = PLATFORM_ROOT / "api"
    offenders = []
    for path in root.rglob("*.py"):
        content = path.read_text(encoding="utf-8")
        if "LiveNode" in content or "account_node(" in content:
            offenders.append(path.relative_to(PLATFORM_ROOT).as_posix())
        if path.name != "engine_client.py" and "book.sqlite" in content:
            offenders.append(path.relative_to(PLATFORM_ROOT).as_posix())
    assert offenders == []

    trading = (root / "exchanges/trading.py").read_text(encoding="utf-8")
    service = (root / "exchanges/service.py").read_text(encoding="utf-8")
    equity = (root / "equity/service.py").read_text(encoding="utf-8")
    subscriptions = (root / "market_data/subscriptions.py").read_text(encoding="utf-8")
    jobs = (root / "market_data/service.py").read_text(encoding="utf-8")
    config = (root / "config.py").read_text(encoding="utf-8")
    assert "manual.execute" not in trading
    assert "queries.account_snapshot" not in service
    assert "engine.connectors.binance.valuation" not in equity
    assert "self.engine.valuation_events(account)" in equity
    assert "subprocess.Popen" not in subscriptions and "self.processes" not in subscriptions
    assert 'market_control("sync"' in subscriptions
    assert "subprocess.Popen" not in jobs and "self.processes" not in jobs
    assert '"job/start"' in jobs and '"job/retry"' in jobs and '"job/cancel"' in jobs
    assert "def recover(" not in jobs and "def _wait(" not in jobs
    assert 'os.getenv("ENGINE_URL", "http://127.0.0.1:8766")' in config


def test_platform_checkout_contains_no_engine_sources_or_root_heavy_lock():
    """Phase 20 must not accidentally reintroduce the monorepo Engine runtime."""
    for relative in (
        "engine", "tests/engine", "docker/engine.Dockerfile",
        "nautilus_support.py", "pyproject.toml", "uv.lock",
        "rust-toolchain.toml",
    ):
        assert not (PLATFORM_ROOT / relative).exists(), relative


def test_engine_pins_are_identical_for_ci_build_and_manual_smoke():
    """Three entrypoints must use one reviewed immutable independent Engine SHA."""
    paths = (
        ".github/workflows/tenant-engine.yml",
        "scripts/build_external_engine.sh",
        "scripts/verify_external_engine.sh",
    )
    observed = {}
    for relative in paths:
        text = (PLATFORM_ROOT / relative).read_text(encoding="utf-8")
        pinned = re.findall(r"\\b[0-9a-f]{40}\\b", text)
        assert len(pinned) == 1, relative
        observed[relative] = pinned[0]
    assert len(set(observed.values())) == 1, observed
