"""Allowlisted, persistent background jobs for the local management console."""

import copy
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4

_FAMILIES = (
    ("trend_following_long", "趋势跟随多头"),
    ("trend_following_short", "趋势跟随空头"),
    ("mean_reversion_long", "均值回归多头"),
    ("mean_reversion_short", "均值回归空头"),
)
STRATEGIES = [
    {
        "id": f"{market}_{key}",
        "label": f"{market.upper()} {label}",
        "symbol": f"{market.upper()}USDT",
        "description": "使用本地已有 K 线进行独立研究回测，结束时间不包含在区间内。",
    }
    for market in ("btc", "eth")
    for key, label in _FAMILIES
]
ACTIONS = [
    {"id": "health", "label": "刷新健康检查", "description": "更新本地采集健康报告。"},
    {"id": "quality", "label": "刷新样本质量", "description": "统计前向观察的样本质量。"},
    {"id": "check_quick", "label": "快速项目检查", "description": "核验冻结文件与代码规范。"},
    {"id": "check_full", "label": "完整项目检查", "description": "核验文件、代码规范及全量测试。"},
    {"id": "backtest", "label": "运行策略回测", "description": "使用本地数据回测指定策略。"},
]
_COMMANDS = {
    "health": ("ops.project_health", "--write"),
    "quality": ("ops.sample_quality",),
    "check_quick": ("ops.check_project", "--development", "--quick"),
    "check_full": ("ops.check_project", "--development"),
}
_LABELS = {item["id"]: item["label"] for item in ACTIONS}
_STRATEGIES = {item["id"]: item for item in STRATEGIES}
_STATUSES = {"running", "succeeded", "failed", "cancelled", "interrupted", "skipped"}
_DATE = re.compile(
    r"\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d{1,6})?)?(?:Z|[+-]\d{2}:\d{2})?)?"
)
LOG_TAIL_BYTES = 64 * 1024
CHECK_TIMEOUT = 30 * 60
BACKTEST_TIMEOUT = 2 * 60 * 60
STOP_GRACE = 2.0


class BusyError(ValueError):
    """A console job is already using the single execution slot."""


def _now():
    return datetime.now(UTC).isoformat()


def _date(value, name):
    if not isinstance(value, str) or not _DATE.fullmatch(value):
        raise ValueError(f"{name} 必须为 ISO 日期或时间")
    try:
        parsed = datetime.fromisoformat(value)
        return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)
    except (ValueError, OverflowError) as exc:
        raise ValueError(f"{name} 不是有效日期或时间") from exc


def _build_command(python, action, parameters):
    if not isinstance(action, str) or action not in _LABELS:
        raise ValueError("未知任务类型")
    if not isinstance(parameters, dict):
        raise TypeError("parameters 必须为对象")
    if action != "backtest":
        if parameters:
            raise ValueError("该任务不接受自定义参数")
        return [python, "-m", *_COMMANDS[action]], {}, _LABELS[action], None
    if set(parameters) - {"strategy", "start", "end", "interval"}:
        raise ValueError("回测只接受 strategy、start、end、interval 参数")
    strategy = parameters.get("strategy")
    if not isinstance(strategy, str) or strategy not in _STRATEGIES:
        raise ValueError("请选择支持的策略")
    start = _date(parameters.get("start"), "start")
    end = _date(parameters.get("end"), "end")
    if not start < end:
        raise ValueError("开始时间必须早于结束时间；结束时间不包含在区间内")
    if end - start > timedelta(days=366):
        raise ValueError("单次回测区间不能超过 366 天")
    interval = parameters.get("interval", "1m")
    if not isinstance(interval, str) or interval not in {"1m", "5m"}:
        raise ValueError("K 线周期只支持 1m 或 5m")
    market, key = strategy.split("_", 1)
    if market == "btc":
        command = [python, "-m", "strategy_python.btc_backtest", key]
        report_name = "btc_current_" + key
    else:
        report_name = key + "_5min"
        command = [python, "-m", f"strategy_python.{report_name}.backtest"]
    normalized = {
        "strategy": strategy,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "interval": interval,
    }
    command += ["--start", normalized["start"], "--end", normalized["end"], "--interval", interval]
    return command, normalized, _STRATEGIES[strategy]["label"] + " 回测", report_name


def _atomic_json(path, value):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, delete=False
        ) as target:
            temporary = Path(target.name)
            json.dump(value, target, ensure_ascii=False, indent=2, allow_nan=False)
            target.write("\n")
            target.flush()
            os.fsync(target.fileno())
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


class JobManager:
    """Run at most one fixed command, retaining history across server restarts."""

    def __init__(self, project: Path, python: str = sys.executable):
        self.project = Path(project).resolve()
        self.python = str(python)
        self.directory = self.project / "data/web_console/jobs"
        self.directory.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._stop_lock = threading.Lock()
        self._jobs = {}
        self._active = None
        self._process = None
        self._thread = None
        self._cancelled = threading.Event()
        self._closed = False
        self._restore()

    def _restore(self):
        for path in self.directory.glob("*.json"):
            try:
                job = json.loads(path.read_text(encoding="utf-8"))
                if (
                    not isinstance(job, dict)
                    or job.get("id") != path.stem
                    or not re.fullmatch(r"[0-9a-f]{32}", path.stem)
                    or job.get("status") not in _STATUSES
                    or job.get("action") not in _LABELS
                ):
                    continue
                if job["status"] == "running":
                    job.update(
                        status="interrupted",
                        finished_at=_now(),
                        error="管理端已重启，先前任务状态无法确认；未操作历史进程。",
                    )
                    _atomic_json(path, job)
                self._jobs[job["id"]] = job
            except (OSError, ValueError, TypeError):
                continue

    def _save(self, job):
        _atomic_json(self.directory / f"{job['id']}.json", job)

    def list_jobs(self):
        with self._lock:
            return copy.deepcopy(
                sorted(
                    self._jobs.values(), key=lambda item: item.get("created_at", ""), reverse=True
                )
            )

    def _log_tail(self, identity):
        try:
            with (self.directory / f"{identity}.log").open("rb") as source:
                size = source.seek(0, os.SEEK_END)
                source.seek(max(0, size - LOG_TAIL_BYTES))
                return source.read(LOG_TAIL_BYTES).decode(
                    "utf-8", errors="replace"
                ), size > LOG_TAIL_BYTES
        except FileNotFoundError:
            return "", False

    def get_job(self, identity):
        with self._lock:
            job = copy.deepcopy(self._jobs[identity])
        job["log"], job["log_truncated"] = self._log_tail(identity)
        return job

    def start(self, action: str, parameters: dict):
        command, parameters, label, report_name = _build_command(self.python, action, parameters)
        with self._lock:
            if self._closed:
                raise RuntimeError("任务管理器已经关闭")
            if self._active is not None:
                raise BusyError("已有任务运行中，请等待完成或先取消")
            job = {
                "id": uuid4().hex,
                "action": action,
                "label": label,
                "status": "running",
                "created_at": _now(),
                "finished_at": None,
                "returncode": None,
                "parameters": parameters,
                "error": None,
                "report_urls": [],
            }
            self._save(job)
            self._jobs[job["id"]] = job
            before = self._known_reports(action)
            try:
                with (self.directory / f"{job['id']}.log").open("ab", buffering=0) as output:
                    process = subprocess.Popen(
                        command,
                        cwd=self.project,
                        stdin=subprocess.DEVNULL,
                        stdout=output,
                        stderr=subprocess.STDOUT,
                        start_new_session=True,
                        env={**os.environ, "PYTHONUNBUFFERED": "1"},
                    )
            except OSError as exc:
                job.update(status="failed", finished_at=_now(), error=str(exc))
                self._save(job)
                return copy.deepcopy(job)
            self._active = job["id"]
            self._process = process
            self._cancelled = threading.Event()
            self._thread = threading.Thread(
                target=self._run,
                args=(job["id"], process, self._cancelled, report_name, before),
                name=f"web-job-{job['id'][:8]}",
                daemon=True,
            )
            self._thread.start()
            return copy.deepcopy(job)

    @staticmethod
    def _group_exists(process):
        try:
            os.killpg(process.pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            # macOS can report EPERM while a signalled group is exiting.
            # Keep waiting; a final SIGKILL must still surface real permission errors.
            return True

    def _stop_group(self, process):
        # Cancellation, timeout and shutdown can converge here at the same time.
        with self._stop_lock:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                return
            deadline = time.monotonic() + STOP_GRACE
            while time.monotonic() < deadline:
                process.poll()  # Reap the leader so a zombie does not keep the group alive.
                if not self._group_exists(process):
                    return
                time.sleep(0.02)
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait(timeout=5)

    def _run(self, identity, process, cancelled, report_name, before):
        with self._lock:
            action = self._jobs[identity]["action"]
        error = None
        try:
            timeout = BACKTEST_TIMEOUT if action == "backtest" else CHECK_TIMEOUT
            try:
                process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                error = f"任务超过 {timeout // 60} 分钟限制，已终止"
                self._stop_group(process)
            # Also clean up any descendants whose leader exited before them.
            if self._group_exists(process):
                self._stop_group(process)
            log, _ = self._log_tail(identity)
            reports = self._reports(action, report_name, log, before)
            if cancelled.is_set():
                status = "cancelled"
            elif error or process.returncode != 0:
                status = "failed"
                error = error or f"任务退出码为 {process.returncode}，请查看日志"
            elif action == "backtest" and not reports:
                if "指定时间范围内没有已有 K 线，跳过本次回测" in log:
                    status = "skipped"
                    error = "指定区间没有可用的本地 K 线，本次未生成回测报告"
                else:
                    status = "failed"
                    error = "进程结束但未发现本次完整回测报告，请查看日志"
            else:
                status = "succeeded"
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            self._stop_group(process)
            status, reports, error = "failed", [], str(exc)
        with self._lock:
            job = self._jobs[identity]
            if cancelled.is_set():
                status = "cancelled"
            if status in {"failed", "cancelled"}:
                reports = []
            job.update(
                status=status,
                finished_at=_now(),
                returncode=process.returncode,
                error=error,
                report_urls=reports,
            )
            try:
                self._save(job)
            finally:
                self._active = None
                self._process = None

    def _known_reports(self, action):
        if action not in {"health", "quality"}:
            return {}
        root = self.project / "data/forward_validation/btc_eth_mrl_20260921"
        result = {}
        for suffix in ("html",):
            path = root / f"{action}.{suffix}"
            try:
                stat = path.stat()
                result[path] = (stat.st_mtime_ns, stat.st_size, stat.st_ino)
            except FileNotFoundError:
                result[path] = None
        return result

    def _url(self, path):
        return "/artifacts/" + quote(path.relative_to(self.project).as_posix(), safe="/")

    def _reports(self, action, report_name, log, before):
        reports = []
        if action == "backtest":
            expected = self.project / "strategy_python/_report/backtest" / report_name
            # Only the report announced by this process is attributed to this job.
            for line in log.splitlines():
                marker = "中文交互报告："
                if not line.startswith(marker):
                    continue
                path = Path(line[len(marker) :].strip())
                path = (self.project / path).resolve() if not path.is_absolute() else path.resolve()
                if (
                    path.name == "report.html"
                    and path.parent.parent == expected
                    and path.is_file()
                    and (path.parent / "summary.json").is_file()
                ):
                    reports.append(self._url(path))
        elif action in {"check_quick", "check_full"}:
            for line in log.splitlines():
                match = re.fullmatch(r'\s*"artifact_directory":\s*(".*")\s*,?', line)
                if match:
                    path = Path(json.loads(match[1])).resolve() / "results.json"
                    if (
                        path.parent.parent == (self.project / "data/project_checks").resolve()
                        and path.is_file()
                    ):
                        relative = Path("data/project_checks") / path.parent.name / path.name
                        reports.append("/artifacts/" + quote(relative.as_posix(), safe="/"))
        else:
            for path, fingerprint in self._known_reports(action).items():
                if fingerprint is not None and fingerprint != before.get(path):
                    reports.append(self._url(path))
        return list(dict.fromkeys(reports))

    def cancel(self, identity):
        with self._lock:
            job = self._jobs[identity]
            if job["status"] != "running" or self._active != identity:
                return copy.deepcopy(job)
            self._cancelled.set()
            process, thread = self._process, self._thread
        self._stop_group(process)
        thread.join(timeout=STOP_GRACE + 8)
        return self.get_job(identity)

    def close(self):
        with self._lock:
            self._closed = True
            process, thread = self._process, self._thread
            if process is not None:
                self._cancelled.set()
        if process is not None:
            self._stop_group(process)
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=STOP_GRACE + 8)
