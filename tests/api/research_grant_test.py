"""No Engine, Docker, exchange or model calls in research grant unit tests."""
import base64
import hmac
import json
from dataclasses import replace

import pytest

from api.config import Settings
from api.db.auth_repository import AuthRepository
from api.db.database import Database
from api.research.grants import ResearchGrantDenied, ResearchGrantIssuer
from api.tenants.repository import TenantRepository

KEY = "tenant-development-research-key-xxxxxxxxxxxx"
DATASET = "futures_um:ETHUSDT:1m"
STRATEGY = "account:desk"


@pytest.fixture
def authorized(tmp_path):
    settings = Settings(tmp_path / "test.db")
    database = Database(settings.database_path)
    database.initialize()
    users = AuthRepository(database)
    owner = users.create_user("research-owner", "fake-hash")
    other = users.create_user("different-owner", "fake-hash")
    tenants = TenantRepository(database)
    development = tenants.create_tenant(
        tenants.template_id_for("development"), "development",
        "Research tenant", "", owner["id"],
    )
    production = tenants.production_for_owner(owner["id"])
    events = []

    def authorized_resources(tenant, datasets, strategies):
        return (
            tenant["id"] == development["id"]
            and all(ref == DATASET for ref in datasets)
            and all(ref == STRATEGY for ref in strategies)
        )

    issuer = ResearchGrantIssuer(
        tenant_repository=tenants,
        signing_key_for_tenant=lambda tenant_id: (
            KEY if tenant_id == str(development["id"]) else ""
        ),
        authorize_resources=authorized_resources,
        record_audit=events.append,
        clock=lambda: 1_700_000_000,
        forbidden_key="administrative-engine-secret",
    )
    return issuer, owner, other, development, production, tenants, events


def issue(authorized, **kwargs):
    issuer, owner, _, development, _, _, _ = authorized
    return issuer.issue(
        actor_user_id=owner["id"], tenant_id=development["id"],
        task_id="research-123", operations=("datasets:read", "backtests:submit"),
        datasets=(DATASET,), strategies=(STRATEGY,),
        **kwargs,
    )


def test_platform_grant_matches_independent_engine_contract(authorized):
    issued = issue(authorized)
    encoded, mac = issued.token.split(".")
    expected = base64.urlsafe_b64encode(
        hmac.digest(KEY.encode(), encoded.encode(), "sha256")
    ).decode().rstrip("=")
    assert hmac.compare_digest(mac, expected)
    data = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
    assert data == {
        "version": 1, "audience": "seealpha-engine-research",
        "tenant_id": issued.tenant_id, "task_id": "research-123",
        "ops": ["datasets:read", "backtests:submit"],
        "datasets": [DATASET], "strategies": [STRATEGY],
        "not_before": 1_700_000_000,
        "expires_at": 1_700_000_600,
    }
    assert "token" not in issued.public()
    assert issued.token not in repr(issued)
    receipt = authorized[-1][0]
    assert "token" not in receipt
    assert receipt["fingerprint"] == issued.fingerprint


def test_owner_purpose_and_tenant_status_gate(authorized):
    issuer, owner, other, development, production, tenants, _ = authorized
    def denied(tenant_id, actor_id):
        with pytest.raises(ResearchGrantDenied):
            issuer.issue(
                actor_user_id=actor_id, tenant_id=tenant_id,
                task_id="research-123", operations=("datasets:read",),
                datasets=(DATASET,),
            )
    denied(development["id"], other["id"])
    denied(production["id"], owner["id"])
    denied(999999, owner["id"])
    tenants.set_status(development["id"], "archived")
    denied(development["id"], owner["id"])


def test_rejects_unsanctioned_scope_budget_and_paths(authorized):
    issuer, owner, _, development, _, _, _ = authorized
    valid = {
        "actor_user_id": owner["id"], "tenant_id": development["id"],
        "task_id": "research-123", "operations": ("datasets:read",),
        "datasets": (DATASET,),
    }
    for change in [
        {"operations": ("orders:submit",)},
        {"operations": ()},
        {"operations": ("datasets:read", "datasets:read")},
        {"datasets": ("futures_um:BTCUSDT:1m",)},
        {"datasets": ()},
        {"task_id": "../other"},
        {"task_id": "research..123"},
        {"ttl_seconds": 3601},
        {"ttl_seconds": True},
        {"ttl_seconds": 1},
        {"strategies": ("account:unissued",)},
        {"operations": ("backtests:submit",), "strategies": ()},
    ]:
        with pytest.raises(ResearchGrantDenied):
            issuer.issue(**(valid | change))


def test_audit_and_key_fail_closed(authorized):
    issuer, owner, _, development, _, _, records = authorized
    def bad_audit(_receipt):
        raise RuntimeError("database not available")

    issuer.record_audit = bad_audit
    with pytest.raises(ResearchGrantDenied, match="audit"):
        issuer.issue(
            actor_user_id=owner["id"], tenant_id=development["id"],
            task_id="research-123", operations=("datasets:read",),
            datasets=(DATASET,),
        )
    assert records == []
    issuer.record_audit = records.append
    issuer.signing_key_for_tenant = lambda _tenant_id: "too-short"
    with pytest.raises(ResearchGrantDenied, match="signing key"):
        issuer.issue(
            actor_user_id=owner["id"], tenant_id=development["id"],
            task_id="research-123", operations=("datasets:read",),
            datasets=(DATASET,),
        )
    issuer.signing_key_for_tenant = lambda _tenant_id: "administrative-engine-secret"
    with pytest.raises(ResearchGrantDenied):
        issuer.issue(
            actor_user_id=owner["id"], tenant_id=development["id"],
            task_id="research-123", operations=("datasets:read",),
            datasets=(DATASET,),
        )
