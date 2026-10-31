"""Runtime relocation preserves history and credentials without touching market data."""

import fcntl
import json
import os
import stat
import subprocess
import sys
import time
from contextlib import contextmanager

import pytest

from api.management import storage
from api.management.jobs import JobManager, NotOwnerError
from api.management.service import management_runtime
from api.management.storage import WebPaths, migrate_legacy, runtime_lock

_LOCK_HOLDER = """
import fcntl
import sys
import time
from pathlib import Path

path, ready = Path(sys.argv[1]), Path(sys.argv[2])
handle = path.open("a")
fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
ready.write_text("held")
while True:
    time.sleep(3600)
"""


def hold_lock(path):
    """Hold path from a separate process until release_lock stops it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    ready = path.with_name(path.name + ".ready")
    ready.unlink(missing_ok=True)
    process = subprocess.Popen(
        [sys.executable, "-c", _LOCK_HOLDER, str(path), str(ready)],
        stderr=subprocess.PIPE,
    )
    process.ready_path = ready
    deadline = time.monotonic() + 5
    while not ready.exists():
        if process.poll() is not None:
            detail = process.stderr.read().decode() if process.stderr is not None else ""
            raise AssertionError(f"lock holder exited {process.returncode}: {detail}")
        if time.monotonic() > deadline:
            process.kill()
            process.wait(timeout=5)
            raise AssertionError("lock holder did not acquire the lock")
        time.sleep(0.02)
    return process


def release_lock(process):
    if process.poll() is None:
        process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)
    if process.stderr is not None:
        process.stderr.close()
    ready = getattr(process, "ready_path", None)
    if ready is not None:
        ready.unlink(missing_ok=True)


@contextmanager
def external_lock(path):
    process = hold_lock(path)
    try:
        yield process
    finally:
        release_lock(process)


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


def test_startup_migrates_before_loading_jobs(tmp_path):
    paths, identity = legacy_files(tmp_path)
    with management_runtime(tmp_path, database=None) as service:
        assert paths.password.is_file()
        assert service.jobs.get_job(identity)["log"] == "original output\n"
        assert service.jobs.executor is True
    assert not paths.legacy.exists()


def test_optional_lock_is_false_while_another_descriptor_holds_it(tmp_path):
    paths = WebPaths(tmp_path)
    paths.lock.parent.mkdir(parents=True)
    with paths.lock.open("a") as held:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with runtime_lock(paths, required=False) as owner:
            assert owner is False
        with pytest.raises(ValueError, match="已在运行"), runtime_lock(paths):
            pytest.fail("required lock must still refuse a second holder")


def test_symlink_lock_is_rejected_when_the_lock_is_optional(tmp_path):
    paths = WebPaths(tmp_path)
    paths.lock.parent.mkdir(parents=True)
    paths.lock.symlink_to(tmp_path / "other.lock")
    with pytest.raises(ValueError, match="符号链接"), runtime_lock(paths, required=False):
        pytest.fail("a symlink lock must not be treated as a free slot")


def test_second_runtime_reads_jobs_without_interrupting_or_signalling(tmp_path, monkeypatch):
    monkeypatch.setattr(os, "killpg", lambda *_args, **_kwargs: pytest.fail("must not signal"))
    with management_runtime(tmp_path, database=None) as owner:
        assert owner.jobs.executor is True
        paths = WebPaths(tmp_path)
        identity = "b" * 32
        (paths.jobs / f"{identity}.json").write_text(
            json.dumps(
                {
                    "id": identity,
                    "action": "check_quick",
                    "status": "running",
                    "created_at": "2026-08-01T00:00:00+00:00",
                }
            )
        )
        (paths.logs / f"{identity}.log").write_text("still running\n")
        with management_runtime(tmp_path, database=None) as follower:
            assert follower.jobs.executor is False
            current = follower.jobs.get_job(identity)
            assert current["status"] == "running"
            assert current["log"] == "still running\n"
            assert current["log_truncated"] is False
            assert follower.state()["jobs"][0]["status"] == "running"
            assert "log" not in follower.state()["jobs"][0]
            with pytest.raises(NotOwnerError, match="管理任务锁"):
                follower.jobs.start("check_quick", {})
            with pytest.raises(ValueError):
                follower.jobs.start("shell", {})
            with pytest.raises(NotOwnerError):
                follower.jobs.cancel(identity)
        stored = json.loads((paths.jobs / f"{identity}.json").read_text())
        assert stored["status"] == "running"
        assert "finished_at" not in stored
    with management_runtime(tmp_path, database=None) as restarted:
        assert restarted.jobs.executor is True
        recovered = restarted.jobs.get_job(identity)
        assert recovered["status"] == "interrupted"
        assert "未操作历史进程" in recovered["error"]
        assert recovered["log"] == "still running\n"


def test_other_process_lock_skips_migration_and_does_not_keep_the_new_lock(tmp_path):
    paths, identity = legacy_files(tmp_path)
    job_path = paths.legacy / "jobs" / f"{identity}.json"
    job = json.loads(job_path.read_text())
    job["status"] = "running"
    job_path.write_text(json.dumps(job))
    with (
        external_lock(paths.legacy / "server.lock"),
        management_runtime(tmp_path, database=None) as service,
    ):
        assert service.jobs.executor is False
        assert service.jobs.list_jobs() == []
        assert not paths.password.exists()
        assert (paths.legacy / "access_password").is_file()
        assert job_path.is_file()
        with paths.lock.open("a") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(handle, fcntl.LOCK_UN)
    assert not paths.password.exists()
    with management_runtime(tmp_path, database=None) as service:
        assert service.jobs.executor is True
        assert paths.password.is_file()
        assert service.jobs.get_job(identity)["status"] == "interrupted"
        assert service.jobs.get_job(identity)["log"] == "original output\n"
    assert not paths.legacy.exists()
