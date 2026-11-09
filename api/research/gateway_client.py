"""Private Platform -> trusted Harness Gateway control-plane client.

The browser must NEVER receive an Engine grant or task Bridge capability.
Only a trusted server-side research-task provisioner may instantiate this.
"""
from __future__ import annotations

import hmac
import json
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import httpx

from .grants import ResearchGrant

GATEWAY_HEADER = "X-SeeAlpha-Gateway-Contract-Version"
GATEWAY_VERSION = "1"


class GatewayRegistrationError(RuntimeError):
    pass


@dataclass(frozen=True)
class BridgeRegistration:
    tenant_id: str
    task_id: str
    expires_at: int
    bridge_token: str = field(repr=False)

    def public(self) -> dict:
        return {
            "tenant_id": self.tenant_id,
            "task_id": self.task_id, "expires_at": self.expires_at,
        }


class GatewayControlClient:
    """Fixed origin, fixed routes, no redirects or unsafe error echoing."""

    def __init__(self, origin: str, control_token: str, *, transport=None):
        url = urlsplit(origin)
        if (
            url.scheme not in {"http", "https"} or not url.hostname
            or url.username or url.password or url.path not in {"", "/"}
            or url.query or url.fragment
        ):
            raise ValueError("Gateway must be a fixed service origin")
        if not isinstance(control_token, str) or len(control_token) < 32:
            raise ValueError("Missing strong Gateway control token")
        self.client = httpx.Client(
            base_url=origin.rstrip("/"), timeout=httpx.Timeout(15),
            follow_redirects=False, trust_env=False, transport=transport,
            headers={
                "Authorization": f"Bearer {control_token}",
                GATEWAY_HEADER: GATEWAY_VERSION,
                "Accept": "application/json",
            },
        )

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def close(self):
        self.client.close()

    def _request(self, method: str, path: str, *, payload: dict | None = None) -> dict:
        try:
            with self.client.stream(method, path, json=payload) as response:
                if response.status_code >= 300 or response.is_redirect:
                    raise GatewayRegistrationError("Trusted research Gateway refused operation")
                if response.headers.get(GATEWAY_HEADER) != GATEWAY_VERSION:
                    raise GatewayRegistrationError("Trusted research Gateway contract mismatch")
                if response.status_code == 204:
                    return {}
                data = bytearray()
                for block in response.iter_bytes():
                    if len(data) + len(block) > 32_000:
                        raise GatewayRegistrationError("Trusted research Gateway response too large")
                    data.extend(block)
        except httpx.RequestError:
            raise GatewayRegistrationError("Trusted research Gateway unavailable") from None
        try:
            result = json.loads(data)
            if not isinstance(result, dict):
                raise ValueError
            return result
        except (ValueError, UnicodeDecodeError):
            raise GatewayRegistrationError("Invalid trusted research Gateway response") from None

    def register(self, grant: ResearchGrant, *, max_calls: int = 100) -> BridgeRegistration:
        if type(max_calls) is not int or not 1 <= max_calls <= 500:
            raise ValueError("Invalid research call budget")
        result = self._request(
            "POST", "/internal/v1/leases",
            payload={
                "tenant_id": grant.tenant_id,
                "task_id": grant.task_id,
                "engine_grant": grant.token,
                "max_calls": max_calls,
            },
        )
        token = result.get("bridge_token")
        if (
            result.get("tenant_id") != grant.tenant_id
            or result.get("task_id") != grant.task_id
            or type(result.get("expires_at")) is not int
            or result["expires_at"] != grant.expires_at
            or not isinstance(token, str) or len(token) < 32
        ):
            raise GatewayRegistrationError("Trusted research Gateway receipt inconsistent")
        return BridgeRegistration(
            tenant_id=grant.tenant_id, task_id=grant.task_id,
            expires_at=grant.expires_at, bridge_token=token,
        )

    def revoke(self, task_id: str) -> None:
        from .grants import _refs
        _refs((task_id,), name="task")
        self._request("DELETE", f"/internal/v1/tasks/{task_id}")
