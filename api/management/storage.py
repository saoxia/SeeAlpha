"""Runtime paths and one-time migration of the former console's local files."""

import fcntl
import hashlib
import os
import re
import shutil
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class WebPaths:
    project: Path

    # access_password, logs/web and .local/state/web/jobs keep the retired console's
    # names so migrate_legacy still moves data/web_console files without splitting history.
    @property
    def jobs(self):
        return self.project / ".local/state/web/jobs"

    @property
    def logs(self):
        return self.project / "logs/web"

    @property
    def password(self):
        return self.project / ".local/config/web/access_password"

    @property
    def lock(self):
        return self.project / ".local/run/web/server.lock"

    @property
    def legacy(self):
        return self.project / "data/web_console"


def _digest(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").digest()


def _sync_directory(path):
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def migrate_legacy(paths):
    """Move known files only; call while holding runtime_lock, with the server stopped."""
    legacy = paths.legacy
    if not legacy.exists():
        return
    if legacy.is_symlink() or (legacy / "jobs").is_symlink():
        raise ValueError("旧 Web 运行目录不能是符号链接，请手动检查后迁移")
    moves = []
    if (legacy / "access_password").exists() or (legacy / "access_password").is_symlink():
        moves.append((legacy / "access_password", paths.password))
    for source in sorted((legacy / "jobs").glob("*")):
        if not re.fullmatch(r"[0-9a-f]{32}\.(json|log)", source.name):
            continue
        destination = paths.jobs if source.suffix == ".json" else paths.logs
        moves.append((source, destination / source.name))
    # Check conflicts before moving anything; never replace a newer password or job.
    for source, target in moves:
        if source.is_symlink() or not source.is_file() or target.is_symlink():
            raise ValueError(f"Web 迁移只接受普通文件：{source.name}")
        if target.exists() and _digest(source) != _digest(target):
            raise ValueError(f"Web 迁移发现同名内容冲突，未覆盖：{target}")
    for source, target in moves:
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not target.exists():
            # Copy on the destination filesystem, then publish without overwriting.
            # Source remains available if copying or publication fails.
            with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as temporary:
                temporary_path = Path(temporary.name)
                try:
                    with source.open("rb") as original:
                        shutil.copyfileobj(original, temporary)
                    temporary.flush()
                    os.fsync(temporary.fileno())
                    if _digest(source) != _digest(temporary_path):
                        raise ValueError(f"Web 迁移校验失败：{source.name}")
                    os.link(temporary_path, target)
                finally:
                    temporary_path.unlink(missing_ok=True)
        if target == paths.password:
            target.chmod(0o600)
        _sync_directory(target.parent)
        source.unlink()


def _close_lock(handle):
    if handle is not None:
        handle.close()


@contextmanager
def runtime_lock(paths, *, required=True):
    """Hold the single management execution lock.

    Migration and job execution require the lock. When another process already
    holds `.local/run/web/server.lock` or the legacy console lock, ``required``
    raises. API startup passes required=False and receives False so the process
    can keep serving; the caller must not migrate or run jobs in that case.
    """
    paths.lock.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if paths.lock.is_symlink():
        raise ValueError("Web 锁文件不能是符号链接")
    lock_file = paths.lock.open("a")
    old_file = None
    legacy_path = None
    acquired = False
    try:
        try:
            fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if paths.legacy.exists():
                if paths.legacy.is_symlink():
                    raise ValueError("旧 Web 运行目录不能是符号链接")
                legacy_path = paths.legacy / "server.lock"
                if legacy_path.is_symlink():
                    raise ValueError("旧 Web 锁文件不能是符号链接")
                old_file = legacy_path.open("a")
                fcntl.flock(old_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except BlockingIOError:
            if required:
                raise ValueError("Web 管理端已在运行，请先停止后再迁移或启动") from None
        if not acquired:
            # Drop a partial hold before yielding. A follower must not keep the
            # new lock while the legacy console still owns its own lock.
            _close_lock(old_file)
            old_file = None
            _close_lock(lock_file)
            lock_file = None
            yield False
            return
        try:
            yield True
        finally:
            # Keep the old lock until shutdown. Preserve any unrecognized files.
            if old_file is not None:
                legacy_path.unlink(missing_ok=True)
                for directory in (paths.legacy / "jobs", paths.legacy):
                    try:
                        directory.rmdir()
                    except OSError:
                        pass
    finally:
        _close_lock(old_file)
        _close_lock(lock_file)
