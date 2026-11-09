"""Management paths and the current single-process execution lock."""

import fcntl
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class WebPaths:
    project: Path

    @property
    def jobs(self):
        return self.project / ".local/state/web/jobs"

    @property
    def logs(self):
        return self.project / "logs/web"

    @property
    def lock(self):
        return self.project / ".local/run/web/server.lock"


@contextmanager
def runtime_lock(paths, *, required=True):
    """Only the lock owner may execute or recover management jobs.

    Other API workers may read without owning the lock. No former-console
    directory, password file, or historical lock is inspected.
    """
    paths.lock.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if paths.lock.is_symlink():
        raise ValueError("Web 锁文件不能是符号链接")
    with paths.lock.open("a") as lock_file:
        try:
            fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            if required:
                raise ValueError("Web 管理端已在运行，请先停止后再启动") from None
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(lock_file, fcntl.LOCK_UN)
