"""Exercise the console worker against real, isolated child processes."""

import json
import os
import signal
import sys
import time
from pathlib import Path

import pytest

from ops import web_jobs
from ops.web_jobs import BusyError, JobManager


@pytest.fixture
def manager(tmp_path):
    jobs = JobManager(tmp_path, python=sys.executable)
    yield jobs
    jobs.close()


def module(project, name, source):
    pieces = name.split(".")
    root = project
    for piece in pieces[:-1]:
        root /= piece
        root.mkdir(exist_ok=True)
        (root / "__init__.py").touch()
    (root / (pieces[-1] + ".py")).write_text(source, encoding="utf-8")


def finished(manager, identity):
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        job = manager.get_job(identity)
        if job["status"] != "running":
            return job
        time.sleep(0.02)
    pytest.fail("Child job did not finish")


def await_file(path):
    deadline = time.monotonic() + 5
    while not path.exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert path.exists()


def backtest_parameters(**overrides):
    return {
        "strategy": "btc_trend_following_long",
        "start": "2026-08-01",
        "end": "2026-08-02",
        **overrides,
    }


@pytest.mark.parametrize(
    "action,parameters",
    [
        ("shell", {}),
        ("health", {"root": "/tmp/anything"}),
        ("health", []),
        ("backtest", backtest_parameters(strategy="../../ops.check_project")),
        ("backtest", backtest_parameters(start="2026-08-01; touch injected")),
        ("backtest", backtest_parameters(start="2026-02-30")),
        ("backtest", backtest_parameters(start="2026-08-03")),
        ("backtest", backtest_parameters(start="2020-01-01")),
        ("backtest", backtest_parameters(interval="1m --check-data")),
        ("backtest", backtest_parameters(interval=[])),
        ("backtest", backtest_parameters(check_data=True)),
    ],
)
def test_rejects_commands_and_unbounded_or_invalid_backtests(manager, action, parameters):
    with pytest.raises((ValueError, TypeError)):
        manager.start(action, parameters)
    assert manager.list_jobs() == []
    assert list(manager.directory.iterdir()) == []


def test_records_output_persists_and_bounds_log(manager):
    module(manager.project, "ops.project_health", "print('x' * 90000)\nprint('done')\n")
    identity = manager.start("health", {})["id"]
    job = finished(manager, identity)
    assert job["status"] == "succeeded"
    assert job["returncode"] == 0
    assert job["log"].endswith("done\n")
    assert len(job["log"].encode()) == web_jobs.LOG_TAIL_BYTES
    assert job["log_truncated"] is True
    assert "log" not in manager.list_jobs()[0]
    restored = JobManager(manager.project)
    try:
        assert restored.get_job(identity) == job
    finally:
        restored.close()


def test_busy_and_cancel_stop_child_and_allow_following_job(manager):
    module(
        manager.project,
        "ops.project_health",
        "import time\nfrom pathlib import Path\nPath('ready').touch()\ntime.sleep(60)\n",
    )
    job = manager.start("health", {})
    assert (
        json.loads((manager.directory / (job["id"] + ".json")).read_text())["status"] == "running"
    )
    await_file(manager.project / "ready")
    with pytest.raises(BusyError):
        manager.start("quality", {})
    cancelled = manager.cancel(job["id"])
    assert cancelled["status"] == "cancelled"
    assert cancelled["returncode"] == -signal.SIGTERM
    module(manager.project, "ops.sample_quality", "print('quality ready')\n")
    following = manager.start("quality", {})
    assert finished(manager, following["id"])["status"] == "succeeded"


def test_exiting_group_permission_query_is_retried(manager, monkeypatch):
    from types import SimpleNamespace

    signals = []

    def killpg(pid, sig):
        signals.append(sig)
        if sig == 0:
            if signals.count(0) == 1:
                raise PermissionError("macOS group exit transition")
            raise ProcessLookupError

    monkeypatch.setattr(web_jobs.os, "killpg", killpg)
    manager._stop_group(SimpleNamespace(pid=123, poll=lambda: -signal.SIGTERM))
    assert signals == [signal.SIGTERM, 0, 0]


def test_project_check_report_links_follow_only_the_configured_data_root(manager):
    external = manager.project.parent / (manager.project.name + "-external")
    (manager.project / "data").rename(external)
    (manager.project / "data").symlink_to(external, target_is_directory=True)
    report = external / "project_checks/run/results.json"
    report.parent.mkdir(parents=True)
    report.write_text("{}")
    log = json.dumps({"artifact_directory": str(report.parent)}, indent=2)
    assert manager._reports("check_quick", None, log, {}) == [
        "/artifacts/data/project_checks/run/results.json"
    ]
    assert manager._reports(
        "check_quick", None, json.dumps({"artifact_directory": str(external)}, indent=2), {}
    ) == []


def test_shutdown_kills_entire_child_group(manager):
    module(
        manager.project,
        "ops.project_health",
        """
import subprocess
import sys
import time
from pathlib import Path
child = subprocess.Popen([sys.executable, '-c',
    "import time; from pathlib import Path; Path('grandchild-ready').touch(); time.sleep(1); Path('escaped').touch(); time.sleep(60)"])
Path('child.pid').write_text(str(child.pid))
time.sleep(60)
""",
    )
    job = manager.start("health", {})
    await_file(manager.project / "grandchild-ready")
    manager.close()
    assert manager.get_job(job["id"])["status"] == "cancelled"
    assert not (manager.project / "escaped").exists()
    pid = int((manager.project / "child.pid").read_text())
    proc_stat = Path(f"/proc/{pid}/stat")
    # An adopted zombie is no longer executing even if init has not reaped it yet.
    assert not proc_stat.exists() or proc_stat.read_text().split()[2] == "Z"
    with pytest.raises(RuntimeError):
        manager.start("health", {})


def test_timeout_fails_and_stops_process(manager, monkeypatch):
    monkeypatch.setattr(web_jobs, "CHECK_TIMEOUT", 0.05)
    module(manager.project, "ops.project_health", "import time\ntime.sleep(60)\n")
    job = finished(manager, manager.start("health", {})["id"])
    assert job["status"] == "failed"
    assert "限制" in job["error"]
    assert job["returncode"] == -signal.SIGTERM


def test_cancel_escalates_for_child_ignoring_termination(manager, monkeypatch):
    monkeypatch.setattr(web_jobs, "STOP_GRACE", 0.1)
    module(
        manager.project,
        "ops.project_health",
        """
import signal
import time
from pathlib import Path
signal.signal(signal.SIGTERM, signal.SIG_IGN)
Path('ready').touch()
time.sleep(60)
""",
    )
    job = manager.start("health", {})
    await_file(manager.project / "ready")
    cancelled = manager.cancel(job["id"])
    assert cancelled["status"] == "cancelled"
    assert cancelled["returncode"] == -signal.SIGKILL


def test_missing_data_is_skipped_and_does_not_claim_external_report(manager):
    module(
        manager.project,
        "strategy_python.btc_backtest",
        """
from pathlib import Path
p = Path('strategy_python/_report/backtest/btc_current_trend_following_long/external')
p.mkdir(parents=True)
(p / 'report.html').write_text('external run')
(p / 'summary.json').write_text('{}')
print('指定时间范围内没有已有 K 线，跳过本次回测')
""",
    )
    job = finished(manager, manager.start("backtest", backtest_parameters())["id"])
    assert job["status"] == "skipped"
    assert job["report_urls"] == []
    assert job["returncode"] == 0


def test_zero_trade_report_is_success_and_links_only_announced_report(manager):
    module(
        manager.project,
        "strategy_python.btc_backtest",
        """
import sys
from pathlib import Path
assert sys.argv[1] == 'trend_following_long'
assert '--check-data' not in sys.argv
p = Path('strategy_python/_report/backtest/btc_current_trend_following_long/run1')
p.mkdir(parents=True)
(p / 'report.html').write_text('zero trades')
(p / 'summary.json').write_text('{"total_orders": 0}')
print('中文交互报告：' + str((p / 'report.html').resolve()))
""",
    )
    job = finished(manager, manager.start("backtest", backtest_parameters())["id"])
    assert job["status"] == "succeeded"
    assert job["report_urls"] == [
        "/artifacts/strategy_python/_report/backtest/btc_current_trend_following_long/run1/report.html"
    ]
    assert job["parameters"]["interval"] == "1m"


def test_nonzero_exit_preserves_error_and_logs(manager):
    module(manager.project, "ops.sample_quality", "print('bad data')\nraise SystemExit(3)\n")
    job = finished(manager, manager.start("quality", {})["id"])
    assert job["status"] == "failed"
    assert job["returncode"] == 3
    assert "bad data" in job["log"]


def test_startup_interrupts_stale_job_without_signalling_pid(tmp_path, monkeypatch):
    identity = "f" * 32
    directory = tmp_path / "data/web_console/jobs"
    directory.mkdir(parents=True)
    (directory / f"{identity}.json").write_text(
        json.dumps(
            {
                "id": identity,
                "action": "health",
                "status": "running",
                "pid": os.getpid(),
                "created_at": "2026-08-01T00:00:00+00:00",
            }
        )
    )
    monkeypatch.setattr(
        os, "killpg", lambda *args: pytest.fail("Must not signal stale process IDs")
    )
    manager = JobManager(tmp_path)
    try:
        assert manager.get_job(identity)["status"] == "interrupted"
        assert json.loads((directory / f"{identity}.json").read_text())["status"] == "interrupted"
    finally:
        manager.close()
