"""File-only service observations; observations never drive data recovery."""

import json
import time

from fastapi import HTTPException

from pipeline.convert_binance_bars import write_record

ROLES = ("collection", "history", "calculation")


def observe(subscriptions, policy, now):
    root = subscriptions.root(policy)
    enabled = {"collection": policy.realtime_enabled, "history": policy.history_enabled,
               "calculation": policy.base_level != "auto" and (policy.realtime_enabled or policy.history_enabled)}
    result = {}
    for role in ROLES:
        item = {"state": "STOPPED", "healthy": False, "backlog": None, "reason": "disabled"}
        if enabled[role]:
            path = root / ("status.json" if role == "collection" else f"{role}/status.json")
            try:
                if path.stat().st_size > 131072:
                    raise ValueError("Oversize status")
                value = json.loads(path.read_text())
                if not isinstance(value, dict) or type(value.get("valid_until_ms")) is not int:
                    raise ValueError("Invalid status")
                item = {**value, "healthy": value.get("state") == "READY", "reason": value.get("reason") or value.get("last_error")}
                if now > value["valid_until_ms"]:
                    item.update(state="DEGRADED", healthy=False, reason="status_expired")
            except (OSError, ValueError, TypeError):
                item.update(state="DEGRADED", reason="status_unavailable")
            processes = subscriptions.processes if role == "collection" else subscriptions.auxiliary
            key = (policy.product, policy.symbol) if role == "collection" else (policy.product, policy.symbol, role)
            process = processes.get(key)
            if process is None or process.poll() is not None:
                item.update(state="DEGRADED", healthy=False, reason="process_not_running")
        result[role] = item
    return result


class ServiceMonitor:
    def __init__(self, manager):
        self.manager = manager
        self.path = manager.project / ".local/state/api/data_services/status.json"

    def sample(self):
        now = int(time.time() * 1000)
        subscriptions = self.manager.subscriptions
        if subscriptions.lease is None:
            with subscriptions.lock:
                try:
                    subscriptions._claim()
                except HTTPException:
                    return self.read()  # Only the owner may publish shared observations.
        items = [{"product": policy.product, "symbol": policy.symbol,
                  "services": observe(subscriptions, policy, now)} for policy in subscriptions.policies or []]
        config_error = subscriptions.last_error
        value = {"checked_ms": now, "valid_until_ms": now + 15000, "items": items,
                 "state": "DEGRADED" if config_error else "READY", "configuration_error": config_error}
        write_record(self.path, value)
        return value

    def read(self):
        try:
            if self.path.stat().st_size > 4 * 1024 * 1024:
                raise ValueError("Oversize monitor")
            value = json.loads(self.path.read_text())
            if int(time.time() * 1000) > value["valid_until_ms"]:
                value.update(state="DEGRADED", reason="monitor_expired")
                for item in value["items"]:
                    for state in item["services"].values():
                        state.update(healthy=False, reason="monitor_expired")
            return value
        except (OSError, ValueError, TypeError, KeyError):
            return {"state": "DEGRADED", "reason": "monitor_unavailable", "items": []}
