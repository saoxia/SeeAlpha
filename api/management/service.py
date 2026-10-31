"""Report index, published check snapshots and the single job runtime."""

import json
import math
import threading
import time
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote

from .artifacts import BACKTESTS, CHECKS, artifact_path
from .jobs import ACTIONS, STRATEGIES, JobManager
from .storage import WebPaths, migrate_legacy, runtime_lock


def snapshot(project, relative, max_age):
    """Read a small published JSON snapshot; never open private databases."""
    try:
        path = Path(project) / relative
        if path.stat().st_size > 2 * 1024 * 1024:
            raise ValueError("状态文件过大")
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(data, dict):
            raise TypeError("状态格式无效")
        timestamp = data.get("checked_ms")
        if timestamp is not None:
            timestamp = float(timestamp) / 1000
        else:
            value = data.get("checked_at") or data.get("created_at")
            timestamp = datetime.fromisoformat(value).timestamp() if value else None
        if timestamp is not None and not math.isfinite(timestamp):
            raise ValueError("状态时间无效")
        age = max(0, time.time() - timestamp) if timestamp is not None else None
        return {
            "data": data,
            "error": None,
            "age_seconds": age,
            "stale": age is None or age > max_age,
        }
    except (OSError, ValueError, TypeError, OverflowError):
        return {
            "data": None,
            "error": "状态文件尚未生成或暂时无法读取",
            "age_seconds": None,
            "stale": True,
        }


class ManagementService:
    """List jobs and reports. Execution stays in the injected job manager."""

    def __init__(self, project, jobs):
        self.project = Path(project).resolve()
        self.jobs = jobs
        self._reports = []
        self._reports_at = 0.0
        self._report_lock = threading.Lock()

    def reports(self):
        with self._report_lock:
            if time.monotonic() - self._reports_at < 10:
                return self._reports
            items = []
            for path in (self.project / BACKTESTS).glob("**/report.html"):
                try:
                    relative = path.relative_to(self.project)
                    artifact_path(self.project, str(relative))
                    modified = path.stat().st_mtime
                    strategy_index = 4 if relative.parts[3] == "portfolio" else 3
                    strategy = relative.parts[strategy_index]
                    run_id = "/".join(relative.parts[strategy_index + 1 : -1])
                    items.append(
                        {
                            "name": strategy,
                            "strategy": strategy,
                            "run_id": run_id,
                            "modified_at": datetime.fromtimestamp(modified, UTC).isoformat(),
                            "url": "/artifacts/" + quote(relative.as_posix()),
                            "summary_url": "/artifacts/"
                            + quote(relative.with_name("summary.json").as_posix())
                            if path.with_name("summary.json").is_file()
                            else None,
                        }
                    )
                except (OSError, ValueError):
                    continue
            repository = getattr(self.jobs, "repository", None)
            if repository is not None:
                repository.refresh_reports(items)
            self._reports = sorted(items, key=lambda item: item["modified_at"], reverse=True)
            self._reports_at = time.monotonic()
            return self._reports

    def state(self):
        reports = self.reports()
        return {
            "server_time": datetime.now(UTC).isoformat(),
            "checks": snapshot(self.project, CHECKS / "latest.json", 86400),
            "strategies": STRATEGIES,
            "actions": ACTIONS,
            "jobs": self.jobs.list_jobs(),
            "reports": reports,
            "report_count": len(reports),
        }


@contextmanager
def management_runtime(project, database):
    """Migrate leftover console files, then own the single in-process job slot."""
    paths = WebPaths(Path(project))
    orm = database if getattr(database, "backend", None) == "orm" else None
    with runtime_lock(paths):
        migrate_legacy(paths)
        jobs = JobManager(paths.project, orm_database=orm)
        try:
            yield ManagementService(paths.project, jobs)
        finally:
            jobs.close()
