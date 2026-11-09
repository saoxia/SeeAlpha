"""Platform-side, internal-only issuer for Engine Research v1 task grants.

No HTTP grant issuance route exists: the opaque bearer MUST stay in trusted
control-plane code, not the browser, DeepSeek Agent, logs, or task artifacts.

The independent Engine verifies every HMAC grant and resource scope.
"""
from __future__ import annotations

import base64
import hmac
import json
import re
import time
from dataclasses import dataclass
from hashlib import sha256
from typing import Callable

AUDIENCE = "seealpha-engine-research"
RESEARCH_VERSION = 1
_ALLOWED = frozenset({
    "datasets:read", "snapshots:create", "snapshots:read",
    "backtests:submit", "backtests:read", "backtests:cancel",
})
_IDENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class ResearchGrantDenied(ValueError):
    """Authorization failed without disclosing a token or signing key."""


@dataclass(frozen=True)
class ResearchGrant:
    """Secret token is only for the trusted gateway registration call."""

    token: str
    task_id: str
    tenant_id: str
    expires_at: int
    fingerprint: str

    def public(self) -> dict:
        return {
            "task_id": self.task_id, "tenant_id": self.tenant_id,
            "expires_at": self.expires_at, "fingerprint": self.fingerprint,
        }

    def __repr__(self) -> str:
        return (
            f"ResearchGrant(task_id={self.task_id!r}, tenant_id={self.tenant_id!r}, "
            f"expires_at={self.expires_at!r}, token=<redacted>)"
        )


def _refs(values: tuple[str, ...], *, name: str) -> list[str]:
    if not isinstance(values, tuple) or len(values) > 100:
        raise ResearchGrantDenied("Invalid research grant scope")
    if (
        any(not isinstance(x, str) or not _IDENT.fullmatch(x) or ".." in x for x in values)
        or len(set(values)) != len(values)
    ):
        raise ResearchGrantDenied("Invalid research grant scope")
    return list(values)


class ResearchGrantIssuer:
    """Trusted, testable authorization and signing primitive.

    All dependencies are injected. Production callers MUST connect
    tenant-specific Engine research keys, an authoritative resource policy,
    and durable audit storage before enabling task provisioning.
    """

    def __init__(
        self,
        *,
        tenant_repository,
        signing_key_for_tenant: Callable[[str], str],
        authorize_resources: Callable[[dict, tuple[str, ...], tuple[str, ...]], bool],
        record_audit: Callable[[dict], None],
        forbidden_key: str = "",
        clock: Callable[[], float] = time.time,
    ):
        self.tenants = tenant_repository
        self.signing_key_for_tenant = signing_key_for_tenant
        self.authorize_resources = authorize_resources
        self.record_audit = record_audit
        self.forbidden_key = forbidden_key
        self.clock = clock

    def issue(
        self,
        *,
        actor_user_id: int,
        tenant_id: int,
        task_id: str,
        operations: tuple[str, ...],
        datasets: tuple[str, ...] = (),
        strategies: tuple[str, ...] = (),
        ttl_seconds: int = 600,
    ) -> ResearchGrant:
        # Never trust caller-provided tenant ownership or tenant purpose.
        if (type(actor_user_id) is not int or actor_user_id < 1
                or type(tenant_id) is not int or tenant_id < 1):
            raise ResearchGrantDenied("Research tenant authorization denied")
        tenant = self.tenants.get_tenant(tenant_id)
        if (
            tenant is None or tenant["id"] != tenant_id
            or tenant["owner_id"] != actor_user_id
            or tenant["purpose"] != "development"
            or tenant["status"] != "active"
        ):
            raise ResearchGrantDenied("Research tenant authorization denied")
        try:
            task = _refs((task_id,), name="task")[0]
            ops = _refs(operations, name="operations")
            ds = _refs(datasets, name="datasets")
            st = _refs(strategies, name="strategies")
            if not ops or not set(ops) <= _ALLOWED:
                raise ValueError
            if (
                ("datasets:read" in ops or "snapshots:create" in ops)
                and not ds
            ):
                raise ValueError
            if "backtests:submit" in ops and (not st or not ds):
                raise ValueError
            if type(ttl_seconds) is not int or not 60 <= ttl_seconds <= 900:
                raise ValueError
        except (IndexError, ValueError) as exc:
            raise ResearchGrantDenied("Invalid research grant scope") from None

        # Fail closed without authoritative dataset and issued-strategy checks.
        try:
            permitted = self.authorize_resources(tenant, tuple(ds), tuple(st))
        except Exception:
            raise ResearchGrantDenied("Research resource authorization unavailable") from None
        if permitted is not True:
            raise ResearchGrantDenied("Research resources not authorized")

        now = int(self.clock())
        key = self.signing_key_for_tenant(str(tenant_id))
        if (
            not isinstance(key, str) or len(key) < 32
            or key.startswith("replace-")
            or (self.forbidden_key and hmac.compare_digest(key, self.forbidden_key))
        ):
            raise ResearchGrantDenied("Research signing key unavailable")

        claims = {
            "version": RESEARCH_VERSION, "audience": AUDIENCE,
            "tenant_id": str(tenant_id), "task_id": task,
            "ops": ops, "datasets": ds, "strategies": st,
            "not_before": now, "expires_at": now + ttl_seconds,
        }
        payload = base64.urlsafe_b64encode(json.dumps(
            claims, sort_keys=True, ensure_ascii=True,
            separators=(",", ":"),
        ).encode("utf-8")).decode("ascii").rstrip("=")
        signature = base64.urlsafe_b64encode(
            hmac.digest(key.encode("utf-8"), payload.encode("ascii"), "sha256"),
        ).decode("ascii").rstrip("=")
        token = payload + "." + signature
        fingerprint = sha256(token.encode("ascii")).hexdigest()[:24]

        # Audit MUST succeed before releasing the bearer to a gateway caller.
        # Never log or persist the raw bearer in the receipt.
        receipt = {
            "event": "research_grant_issued",
            "actor_user_id": actor_user_id,
            "tenant_id": str(tenant_id), "task_id": task,
            "ops": ops, "datasets": ds, "strategies": st,
            "not_before": now, "expires_at": now + ttl_seconds,
            "fingerprint": fingerprint,
        }
        try:
            self.record_audit(receipt)
        except Exception:
            raise ResearchGrantDenied("Research grant audit unavailable") from None
        return ResearchGrant(
            token=token, task_id=task,
            tenant_id=str(tenant_id), expires_at=now + ttl_seconds,
            fingerprint=fingerprint,
        )
