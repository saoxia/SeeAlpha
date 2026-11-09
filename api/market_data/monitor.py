"""File-only service observations; observations never drive data recovery."""

import json

from fastapi import HTTPException

ROLES = ("collection", "history", "calculation")


def observe(subscriptions, policy, now):
    root = subscriptions.root(policy)
    enabled = {"collection": policy.realtime_enabled, "history": policy.history_enabled,
               "calculation": policy.product == "futures_um" and policy.base_level != "auto" and (policy.realtime_enabled or policy.history_enabled)}
    result = {}
    for role in ROLES:
        item = {"state": "STOPPED", "healthy": False, "backlog": None, "reason": "disabled"}
        if enabled[role] and policy.symbol != "ALL":
            path = root / ("status.json" if role == "collection" else f"{role}/status.json")
            try:
                if path.stat().st_size > 131072:
                    raise ValueError("Oversize status")
                value = json.loads(path.read_text())
                if not isinstance(value, dict) or type(value.get("valid_until_ms")) is not int:
                    raise ValueError("Invalid status")
                item = {**value, "healthy": value.get("state") == "READY", "reason": value.get("reason") or value.get("last_error")}
                expired_option = (policy.product == "options" and value.get("reason") == "expired"
                                  and value.get("expiry_ms", float("inf")) <= now)
                if now > value["valid_until_ms"] and not expired_option:
                    item.update(state="DEGRADED", healthy=False, reason="status_expired")
            except (OSError, ValueError, TypeError):
                item.update(state="DEGRADED", reason="status_unavailable")
            # File-only compatibility observations cannot determine process
            # ownership. Engine HTTP is authoritative for live worker state.
            if item.get("reason") == "expired" and item.get("expiry_ms", float("inf")) <= now:
                item.update(state="STOPPED", healthy=True)
        result[role] = item
    return result


class ServiceMonitor:
    """Platform reads Engine-owned monitoring state; it never samples or writes it."""

    def __init__(self, manager):
        self.manager = manager

    def read(self):
        engine = getattr(self.manager, "engine", None)
        if engine is None:
            raise HTTPException(503, "行情 Engine 未配置，无法读取服务监控。")
        return engine.market_control("services")
