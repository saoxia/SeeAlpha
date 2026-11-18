"""Offline safety and CLI checks for the local installation entry points."""
import os
import socket
import subprocess
from pathlib import Path

import pytest

from scripts.local_runtime import LocalRuntime

ROOT = Path(__file__).resolve().parents[2]


def test_local_launcher_help_and_shell_syntax():
    for path in (ROOT / "seealpha", ROOT / "scripts/install.sh"):
        subprocess.run(["bash", "-n", str(path)], check=True)
    help_text = subprocess.run(
        ["bash", str(ROOT / "seealpha"), "help"],
        check=True, capture_output=True, text=True, cwd=ROOT,
    ).stdout
    for action in ("install", "start", "stop", "status", "doctor", "admin"):
        assert action in help_text


def test_install_refuses_inherited_external_database():
    env = os.environ.copy()
    env["DATABASE_URL"] = "postgresql://example.invalid/production"
    result = subprocess.run(
        ["bash", str(ROOT / "scripts/install.sh")],
        capture_output=True, text=True, env=env, cwd=ROOT,
    )
    assert result.returncode == 2
    assert "DATABASE_URL" in result.stderr


def test_private_config_rejects_world_readable_and_symlink(tmp_path):
    runtime = LocalRuntime(tmp_path)
    secret = tmp_path / "token"
    secret.write_text("secret")
    secret.chmod(0o644)
    with pytest.raises(RuntimeError, match="600"):
        runtime._private_file(secret)
    secret.chmod(0o600)
    assert runtime._private_file(secret) == "secret"
    link = tmp_path / "linked"
    link.symlink_to(secret)
    with pytest.raises(RuntimeError, match="符号链接"):
        runtime._private_file(link)


def test_local_config_rejects_external_database_url(tmp_path):
    runtime = LocalRuntime(tmp_path)
    runtime.db_url_file.parent.mkdir(parents=True)
    runtime.db_url_file.write_text("postgresql://server.example/production")
    runtime.db_url_file.chmod(0o600)
    with pytest.raises(RuntimeError, match="本机隔离 SQLite"):
        runtime.check_config()


def test_stop_never_signals_foreign_pid(tmp_path, monkeypatch):
    runtime = LocalRuntime(tmp_path)
    runtime.pid_dir.mkdir(parents=True)
    pid_file = runtime._pid_file("api")
    pid_file.write_text(f"{os.getpid()}\n")
    signals = []

    def forbid_signal(pid, signum):
        signals.append((pid, signum))
        raise AssertionError("must not signal foreign process")

    monkeypatch.setattr(runtime, "_owned", lambda service, pid: False)
    monkeypatch.setattr(os, "kill", forbid_signal)
    assert runtime._stop_one("api")
    assert not pid_file.exists()
    assert not signals


def test_start_port_guard_detects_local_listener():
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        assert not LocalRuntime._port_available(server.getsockname()[1])


def test_start_refuses_missing_local_config(tmp_path):
    runtime = LocalRuntime(tmp_path)
    with pytest.raises(RuntimeError, match="缺少安装配置"):
        runtime.start()
