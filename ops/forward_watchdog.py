"""Debounced local alerts and bounded service recovery, outside the frozen collector."""

import argparse
import fcntl
import json
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from . import project_health
from .project_health import DEFAULT_ROOT, HOUR, MINUTE, atomic_write, read_json
from .study_backup import BACKUPS


def transition(previous, problems, now):
    """Only notify changes; three minutes of a transient fault are required."""
    codes = sorted(problems)
    same_condition = bool(previous.get("observed_codes")) == bool(codes)
    first = previous.get("first_bad_ms") if same_condition else now
    first = first if first is not None else now
    urgent = any(
        c.startswith(("frozen_", "unreadable", "capacity", "review_overdue")) for c in codes
    )
    ready = bool(codes) and (urgent or now - first >= 3 * MINUTE)
    active = previous.get("active_codes", [])
    changed = ready and codes != active
    # Require two minutes of normal health before announcing recovery.
    recovered = not codes and bool(active) and now - first >= 2 * MINUTE
    if changed or recovered:
        active = codes if changed else []
    event = None
    if changed or recovered:
        event = {
            "id": str(now),
            "at_ms": now,
            "state": "attention" if changed else "recovered",
            "codes": active,
            "message": "；".join(problems[c] for c in active) if active else "采集运维状态已恢复",
        }
    return {
        "observed_codes": codes,
        "first_bad_ms": first,
        "active_codes": active,
        "last_event": event or previous.get("last_event"),
        "last_restart_ms": previous.get("last_restart_ms"),
    }, event


def restart_allowed(health, state, now, enabled):
    codes = {p["code"] for p in health["problems"]}
    forbidden = {"frozen_source_changed", "rules_changed", "unreadable"}
    return (
        enabled
        and not codes & forbidden
        and bool(codes & {"stale_heartbeat", "stopped"})
        and now < health.get("review_ms", 0)
        and now - (state.get("last_restart_ms") or 0) >= 30 * MINUTE
        and now - state["first_bad_ms"] >= 3 * MINUTE
    )


def inspect(root=DEFAULT_ROOT, *, recover=False, backups=BACKUPS):
    root = Path(root)
    with (root / "operations.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        # The independent health timer publishes that page; avoid racing its change log.
        health = project_health.inspect(root, write=False)
        now = health["checked_ms"]
        previous_path = root / "operations.json"
        previous = read_json(previous_path) if previous_path.exists() else {}
        problems = {p["code"]: p["text"] for p in health["problems"]}
        manifest = read_json(root / "manifest.json")
        completed = (root / "review_completion.json").exists()
        host = None
        host_install = root / "windows_guard_install.json"
        if host_install.exists() and not completed:
            try:
                host = read_json(read_json(host_install)["status_path"])
                if now - host["checked_ms"] > 3 * MINUTE or not host.get("keep_awake"):
                    problems["host_guard_stale"] = "Windows保活守护心跳过期或未成功保持唤醒"
                elif not host.get("wsl_reachable"):
                    problems["host_guard_failed"] = "Windows守护报告WSL保活失败"
            except (OSError, ValueError, KeyError):
                problems["host_guard_unreadable"] = "无法读取Windows保活守护状态"
        if now >= manifest["review_ms"]:
            # Collection is intentionally stopped while the independent reviewer works.
            problems = {k: v for k, v in problems.items() if k.startswith("frozen_")}
            if not completed and now > manifest["review_ms"] + HOUR:
                problems["review_overdue"] = "到期评估尚未成功完成，请查看评估服务"
        quality_path = root / "quality.json"
        week_accepted = False
        if quality_path.exists():
            quality = read_json(quality_path)
            week_accepted = len(quality["markets"]) == 2 and all(
                market["operations_week"]["passed"] for market in quality["markets"]
            )
            if not completed and now - quality["checked_ms"] > 2 * HOUR:
                problems["quality_stale"] = "样本质量统计超过两小时未更新"
            for market in quality["markets"]:
                if market["remaining_missed_budget"] < 0:
                    problems["capacity:coverage:" + market["symbol"]] = (
                        market["symbol"] + " 已无法达到本轮覆盖门槛；保留原结束时间与不足结论"
                    )
        else:
            problems["quality_missing"] = "尚无样本质量报告"
        backup_path = Path(backups) / "latest.json"
        if not backup_path.exists() or now / 1000 - backup_path.stat().st_mtime > 26 * 3600:
            problems["backup_stale"] = "最近26小时没有通过恢复核验的备份"
        used = sum(p.stat().st_size for p in root.rglob("*") if p.is_file())
        free = shutil.disk_usage(root).free
        if used >= 18 * 1024**3 or free < 5 * 1024**3:
            problems["capacity:disk"] = "研究接近20GiB上限或磁盘余量不足5GiB"
        state, event = transition(previous, problems, now)
        action = None
        if recover and "capacity:disk" not in problems:
            enabled = subprocess.run(
                ["systemctl", "--user", "is-enabled", "btc-eth-mrl-forward.service"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            ).stdout.strip() in {"enabled", "enabled-runtime"}
            if restart_allowed(health, state, now, enabled):
                result = subprocess.run(
                    ["systemctl", "--user", "restart", "btc-eth-mrl-forward.service"],
                    capture_output=True,
                    text=True,
                    timeout=65,
                    check=False,
                )
                state["last_restart_ms"] = now
                action = {
                    "at_ms": now,
                    "action": "restart_collector",
                    "returncode": result.returncode,
                }
                with (root / "operations_actions.jsonl").open("a") as history:
                    history.write(json.dumps(action) + "\n")
        state.update(
            {
                "checked_ms": now,
                "problems": problems,
                "storage_bytes": used,
                "free_bytes": free,
                "review_completed": completed,
                "week_accepted": week_accepted or previous.get("week_accepted", False),
                "windows_guard": host,
                "last_action": action or previous.get("last_action"),
            }
        )
        metrics = root / "operations_metrics"
        metrics.mkdir(exist_ok=True)
        day = datetime.fromtimestamp(now / 1000, UTC).strftime("%Y-%m-%d")
        with (metrics / (day + ".jsonl")).open("a") as history:
            history.write(
                json.dumps(
                    {
                        "checked_ms": now,
                        "clock_roundtrip_ms": health.get("clock_roundtrip_ms"),
                        "healthy": health["healthy"],
                        "problems": sorted(problems),
                        "markets": [
                            {
                                k: market[k]
                                for k in (
                                    "symbol",
                                    "trade_lag_seconds",
                                    "bar_lag_seconds",
                                    "prospective_quotes",
                                    "missed_decisions",
                                )
                            }
                            for market in health["markets"]
                        ],
                    }
                )
                + "\n"
            )
        if week_accepted and not previous.get("week_accepted") and not state["active_codes"]:
            event = {
                "id": str(now),
                "at_ms": now,
                "state": "milestone",
                "codes": [],
                "message": "两市场近完整7天报价覆盖均达到99%的运维目标；研究仍按原定日期评估",
            }
            state["last_event"] = event
        if completed and not previous.get("review_completed"):
            event = {
                "id": str(now),
                "at_ms": now,
                "state": "completed",
                "codes": [],
                "message": "90天前向观察的到期评估已完成，请查看评估报告",
            }
            state["last_event"] = event
        if event:
            with (root / "operations_events.jsonl").open("a") as history:
                history.write(json.dumps(event, ensure_ascii=False) + "\n")
        atomic_write(previous_path, json.dumps(state, ensure_ascii=False, indent=2))
        return state


def main():
    parser = argparse.ArgumentParser(description="持续故障提示与有界重启，不跳过行情或更改研究")
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--recover", action="store_true")
    args = parser.parse_args()
    print(json.dumps(inspect(args.root, recover=args.recover), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
