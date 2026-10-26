"""Project checks use the same source scope outside Git checkouts."""

import json
import subprocess
import sys

from ops import check_project


def test_development_check_explicitly_omits_forward_runtime_verification(tmp_path, monkeypatch):
    commands = []
    monkeypatch.setattr(check_project, "PROJECT", tmp_path)
    monkeypatch.setattr(check_project, "verify_sources", lambda: {"failures": []})
    monkeypatch.setattr(sys, "argv", ["check_project", "--development", "--quick"])

    def run(command, **kwargs):
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, "passed", "")

    monkeypatch.setattr(check_project.subprocess, "run", run)
    assert check_project.main() == 0
    assert len(commands) == 1
    assert commands[0][2] == "ruff"
    report = json.loads((tmp_path / "data/project_checks/latest.json").read_text())
    assert report["development"] is True


def test_lint_excludes_runtime_and_history_without_hiding_source_errors(tmp_path, monkeypatch):
    (tmp_path / "pyproject.toml").write_text(
        '[tool.ruff]\nextend-exclude = ["strategy_python/_optimization", '
        '"strategy_python/_report/analysis", "strategy_python/_report/backtest"]\n'
        '[tool.ruff.lint]\nselect = ["F821"]\n'
    )
    runtime = (".local", ".venv", "data", "logs", "reports")
    history = (
        "strategy_python/_optimization",
        "strategy_python/_report/analysis",
        "strategy_python/_report/backtest",
    )
    for directory in (*runtime, *history):
        path = tmp_path / directory
        path.mkdir(parents=True, exist_ok=True)
        (path / "ignored.py").write_text("unrelated_missing_name\n")
    (tmp_path / "active.py").write_text("active_missing_name\n")
    monkeypatch.setattr(check_project, "PROJECT", tmp_path)
    monkeypatch.setattr(check_project, "verify_sources", lambda: {"failures": []})
    monkeypatch.setattr(sys, "argv", ["check_project", "--quick"])
    run = subprocess.run

    def run_check(command, **kwargs):
        if command[2] == "ruff":
            # Disabling Git ignores reproduces deployment discovery, even when
            # a sandbox happens to place an empty .git in an ancestor folder.
            return run(command + ["--no-respect-gitignore", "--output-format", "json"], **kwargs)
        return subprocess.CompletedProcess(command, 0, "verified\n", "")

    monkeypatch.setattr(check_project.subprocess, "run", run_check)
    assert check_project.main() == 1
    result = json.loads((tmp_path / "data/project_checks/latest.json").read_text())
    lint = next(step for step in result["steps"] if step["name"] == "lint")
    diagnostics = json.loads((tmp_path / lint["log"]).read_text())
    assert lint["passed"] is False
    assert len(diagnostics) == 1
    assert diagnostics[0]["filename"] == str(tmp_path / "active.py")
    assert diagnostics[0]["code"] == "F821"
