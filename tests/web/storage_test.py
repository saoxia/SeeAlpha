"""Runtime relocation preserves history and credentials without touching market data."""

import fcntl
import json
import stat
import sys

import pytest

from web import server, storage
from web.jobs import JobManager
from web.storage import WebPaths, migrate_legacy, runtime_lock


def legacy_files(project):
    paths = WebPaths(project)
    directory = paths.legacy / "jobs"
    directory.mkdir(parents=True)
    identity = "a" * 32
    job = {"id": identity, "action": "check_quick", "status": "succeeded"}
    (directory / f"{identity}.json").write_text(json.dumps(job))
    (directory / f"{identity}.log").write_text("original output\n")
    (paths.legacy / "access_password").write_text("original-password-123456\n")
    return paths, identity


def test_migrate_external_legacy_preserves_history_password_and_separates_logs(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    external = tmp_path / "external"
    external.mkdir()
    (project / "data").symlink_to(external, target_is_directory=True)
    paths, identity = legacy_files(project)
    (external / "market.bin").write_bytes(b"market data")
    for _ in range(2):
        with runtime_lock(paths):
            migrate_legacy(paths)
    manager = JobManager(project)
    try:
        job = manager.get_job(identity)
        assert job["status"] == "succeeded"
        assert job["log"] == "original output\n"
        assert list(paths.jobs.glob("*.log")) == []
        assert paths.password.read_text() == "original-password-123456\n"
        assert stat.S_IMODE(paths.password.stat().st_mode) == 0o600
        assert not paths.legacy.exists()
        assert (external / "market.bin").read_bytes() == b"market data"
    finally:
        manager.close()


@pytest.mark.parametrize("missing_data", ["absent", "unmounted"])
def test_fresh_runtime_does_not_create_data(tmp_path, missing_data):
    if missing_data == "unmounted":
        (tmp_path / "data").symlink_to(tmp_path / "unmounted/data", target_is_directory=True)
    paths = WebPaths(tmp_path)
    with runtime_lock(paths):
        migrate_legacy(paths)
        manager = JobManager(tmp_path)
        manager.close()
    assert not (tmp_path / "data").exists()
    assert not (tmp_path / "unmounted").exists()
    assert paths.jobs.is_dir() and paths.logs.is_dir()


def test_conflicting_password_prevents_any_migration(tmp_path):
    paths, identity = legacy_files(tmp_path)
    paths.password.parent.mkdir(parents=True)
    paths.password.write_text("different-password-123456")
    with runtime_lock(paths), pytest.raises(ValueError, match="同名内容冲突"):
        migrate_legacy(paths)
    assert (paths.legacy / "jobs" / f"{identity}.json").is_file()
    assert (paths.legacy / "access_password").is_file()
    assert paths.password.read_text() == "different-password-123456"


def test_copy_failure_keeps_source_and_allows_retry(tmp_path, monkeypatch):
    paths, identity = legacy_files(tmp_path)
    original = storage.shutil.copyfileobj

    def fail(*args):
        raise OSError("copy interrupted")

    with runtime_lock(paths):
        monkeypatch.setattr(storage.shutil, "copyfileobj", fail)
        with pytest.raises(OSError, match="copy interrupted"):
            migrate_legacy(paths)
        assert not paths.password.exists()
        assert list(paths.password.parent.iterdir()) == []
        assert (paths.legacy / "access_password").is_file()
        monkeypatch.setattr(storage.shutil, "copyfileobj", original)
        migrate_legacy(paths)
    assert (paths.jobs / f"{identity}.json").is_file()


@pytest.mark.parametrize("legacy", [False, True])
def test_active_server_blocks_migration(tmp_path, legacy):
    paths, _ = legacy_files(tmp_path)
    lock_path = paths.legacy / "server.lock" if legacy else paths.lock
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(ValueError, match="已在运行"), runtime_lock(paths):
            pytest.fail("Must not enter migration while a server is running")
    assert (paths.legacy / "access_password").is_file()


def test_startup_migrates_before_loading_jobs_and_uses_new_password_path(tmp_path, monkeypatch):
    paths, identity = legacy_files(tmp_path)
    monkeypatch.setattr(server, "PROJECT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["web.server"])

    def serve(args, parser):
        assert args.password_file == paths.password
        assert paths.password.is_file()
        manager = JobManager(tmp_path)
        try:
            assert manager.get_job(identity)["log"] == "original output\n"
        finally:
            manager.close()
        return 0

    monkeypatch.setattr(server, "_serve", serve)
    assert server.main() == 0
    assert not paths.legacy.exists()
