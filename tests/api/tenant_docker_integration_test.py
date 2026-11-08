"""Optional real-Docker lifecycle smoke, run only in the dedicated CI Docker job."""
from __future__ import annotations

import os
from pathlib import Path

import httpx
import pytest

from api.tenant_docker import DockerTenantManager

pytestmark = pytest.mark.skipif(
    os.environ.get("SEEALPHA_TEST_DOCKER") != "1",
    reason="requires Docker daemon, built seealpha-engine:ci image, and disposable CI host",
)


def test_real_docker_isolates_tenant_ports_tokens_and_data(tmp_path):
    root = tmp_path / "tenants"
    manager = DockerTenantManager(
        root, image="seealpha-engine:ci",
        production_cpus="1", production_memory="1g",
        development_cpus="1", development_memory="1g",
        log_root=tmp_path / "public" / "logs",
    )
    # This test must never touch a user's existing engine containers.
    tenants = ("908761", "908762")
    default_data = tmp_path / "existing-data"
    default_data.mkdir()
    (default_data / "marker").write_text("unchanged", encoding="utf-8")
    manager.bind_primary_data(tenants[0], default_data, tmp_path / "jobs")
    try:
        manager.ensure(tenants[0], "production")
        manager.ensure(tenants[1], "development")
        prod_url, prod_token = manager.endpoint(tenants[0])
        dev_url, dev_token = manager.endpoint(tenants[1])
        assert prod_url != dev_url and prod_token != dev_token
        for tenant_id, url, token in (
            (tenants[0], prod_url, prod_token),
            (tenants[1], dev_url, dev_token),
        ):
            headers = {"Authorization": f"Bearer {token}"}
            assert httpx.get(url + "/health", headers=headers).status_code == 200
            other = tenants[1] if tenant_id == tenants[0] else tenants[0]
            assert httpx.get(url + f"/tenants/{other}/export", headers=headers).status_code == 403
            assert httpx.get(url + "/health").status_code == 401
            result = httpx.put(
                url + f"/tenants/{tenant_id}",
                headers=headers, json={"purpose": "production" if tenant_id == tenants[0] else "development"},
            )
            assert result.status_code == 200, result.text
        mounts_a = {
            m["Destination"]: Path(m["Source"]).resolve()
            for m in manager._inspect(manager._name(tenants[0]))["Mounts"]
        }
        mounts_b = {
            m["Destination"]: Path(m["Source"]).resolve()
            for m in manager._inspect(manager._name(tenants[1]))["Mounts"]
        }
        assert mounts_a["/app/data"] == default_data.resolve()
        assert mounts_b["/app/data"] == (root / tenants[1] / "data").resolve()
        assert mounts_a["/app/data"] != mounts_b["/app/data"]
        assert mounts_a["/app/.local/state/api/data_jobs"] == (tmp_path / "jobs").resolve()
    finally:
        for tenant_id in tenants:
            manager.stop(tenant_id, remove=True)
    assert (default_data / "marker").read_text() == "unchanged"
    assert (root / tenants[1] / "data").is_dir()
