"""Single management lock and restart recovery, without old-console relocation."""

import fcntl
import json
import os
import subprocess
import sys
import time
from contextlib import contextmanager

import pytest

from api.management.jobs import JobManager, NotOwnerError
from api.management.service import management_runtime
from api.management.storage import WebPaths, runtime_lock

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
    """Hold a current runtime lock from another process."""
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


@pytest.mark.parametrize("missing_data", ["absent", "unmounted"])
def test_fresh_runtime_does_not_create_market_data(tmp_path, missing_data):
    if missing_data == "unmounted":
        (tmp_path / "data").symlink_to(tmp_path / "unmounted/data", target_is_directory=True)
    paths = WebPaths(tmp_path)
    with runtime_lock(paths):
        manager = JobManager(tmp_path)
        manager.close()
    assert not (tmp_path / "data").exists()
    assert not (tmp_path / "unmounted").exists()
    assert paths.jobs.is_dir() and paths.logs.is_dir()


def test_optional_lock_is_false_while_another_descriptor_holds_it(tmp_path):
    paths = WebPaths(tmp_path)
    paths.lock.parent.mkdir(parents=True)
    with paths.lock.open("a") as held:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with runtime_lock(paths, required=False) as owner:
            assert owner is False
        with pytest.raises(ValueError, match="已在运行"), runtime_lock(paths):
            pytest.fail("required lock must refuse a second holder")


def test_symlink_lock_is_rejected_when_optional(tmp_path):
    paths = WebPaths(tmp_path)
    paths.lock.parent.mkdir(parents=True)
    paths.lock.symlink_to(tmp_path / "other.lock")
    with pytest.raises(ValueError, match="符号链接"), runtime_lock(paths, required=False):
        pytest.fail("symlink lock must not be treated as a free slot")


def test_second_runtime_reads_jobs_without_interrupting_or_signalling(tmp_path, monkeypatch):
    monkeypatch.setattr(os, "killpg", lambda *_args, **_kwargs: pytest.fail("must not signal"))
    with management_runtime(tmp_path, database=None) as owner:
        assert owner.jobs.executor is True
        paths = WebPaths(tmp_path)
        identity = "b" * 32
        (paths.jobs / f"{identity}.json").write_text(
            json.dumps({
                "id": identity, "action": "check_quick", "status": "running",
                "created_at": "2026-08-01T00:00:00+00:00",
            })
        )
        (paths.logs / f"{identity}.log").write_text("still running\n")
        with management_runtime(tmp_path, database=None) as follower:
            assert follower.jobs.executor is False
            current = follower.jobs.get_job(identity)
            assert current["status"] == "running"
            assert current["log"] == "still running\n"
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


def test_other_process_lock_follower_does_not_take_or_release_owner_lock(tmp_path):
    paths = WebPaths(tmp_path)
    with external_lock(paths.lock):
        with management_runtime(tmp_path, database=None) as follower:
            assert follower.jobs.executor is False
            assert follower.jobs.list_jobs() == []
            with pytest.raises(NotOwnerError):
                follower.jobs.start("check_quick", {})
        with runtime_lock(paths, required=False) as owner:
            assert owner is False
    with management_runtime(tmp_path, database=None) as owner:
        assert owner.jobs.executor is True


def test_retired_console_directory_is_ignored_without_side_effects(tmp_path):
    paths = WebPaths(tmp_path)
    retired = tmp_path / "data/web_console"
    retired.mkdir(parents=True)
    (retired / "access_password").write_text("retired-password\n")
    (retired / "server.lock").write_text("retired-lock\n")
    with external_lock(retired / "server.lock"):
        with management_runtime(tmp_path, database=None) as owner:
            assert owner.jobs.executor is True
            assert owner.jobs.list_jobs() == []
    assert (retired / "access_password").read_text() == "retired-password\n"
    assert (retired / "server.lock").read_text() == "retired-lock\n"
    assert paths.lock.exists()
