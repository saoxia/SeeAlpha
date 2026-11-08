"""Project checks use the same source scope outside Git checkouts."""

import json
import subprocess
import sys

from api.management import jobs as check_project


def test_project_check_only_runs_source_style_and_requested_tests(tmp_path, monkeypatch):
    commands = []
    monkeypatch.setattr(check_project, "PROJECT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["check_project", "--quick"])

    def run(command, **kwargs):
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, "passed", "")

    monkeypatch.setattr(check_project.subprocess, "run", run)
    assert check_project.run_checks(quick=True) == 0
    assert len(commands) == 2
    assert commands[0][2] == "ruff"
    report = json.loads((tmp_path / "logs/web/checks/latest.json").read_text())
    assert report["passed"] is True and report["full_tests"] is False
    assert not (tmp_path / "data").exists()


def test_lint_excludes_runtime_and_history_without_hiding_source_errors(tmp_path, monkeypatch):
    (tmp_path / "pyproject.toml").write_text(
        '[tool.ruff]\nextend-exclude = ["research_archive/optimization", '
        '"research_archive/analysis"]\n'
        '[tool.ruff.lint]\nselect = ["F821"]\n'
    )
    runtime = (".local", ".venv", "data", "logs", "reports")
    history = (
        "research_archive/optimization",
        "research_archive/analysis",
    )
    for directory in (*runtime, *history):
        path = tmp_path / directory
        path.mkdir(parents=True, exist_ok=True)
        (path / "ignored.py").write_text("unrelated_missing_name\n")
    (tmp_path / "active.py").write_text("active_missing_name\n")
    monkeypatch.setattr(check_project, "PROJECT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["check_project", "--quick"])
    run = subprocess.run

    def run_check(command, **kwargs):
        if command[2] == "ruff":
            # Disabling Git ignores reproduces deployment discovery, even when
            # a sandbox happens to place an empty .git in an ancestor folder.
            return run(command + ["--no-respect-gitignore", "--output-format", "json"], **kwargs)
        return subprocess.CompletedProcess(command, 0, "verified\n", "")

    monkeypatch.setattr(check_project.subprocess, "run", run_check)
    assert check_project.run_checks(quick=True) == 1
    result = json.loads((tmp_path / "logs/web/checks/latest.json").read_text())
    lint = next(step for step in result["steps"] if step["name"] == "lint")
    diagnostics = json.loads((tmp_path / lint["log"]).read_text())
    assert lint["passed"] is False
    assert len(diagnostics) == 1
    assert diagnostics[0]["filename"] == str(tmp_path / "active.py")
    assert diagnostics[0]["code"] == "F821"
