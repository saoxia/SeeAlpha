"""Explicitly import legacy parameters without transferring runtime/job state."""

import argparse
import fcntl
import json
from pathlib import Path

from api.config import Settings
from api.db.database import create_database
from api.tenants.repository import TenantRepository
from core.paths import PROJECT_ROOT

from .contracts import DataPolicy
from .repository import PolicyRepository


def main():
    parser = argparse.ArgumentParser(description="Import pair configuration; preserve existing database parameters")
    parser.add_argument("path", type=Path)
    args = parser.parse_args()
    if args.path.stat().st_size > 1024 * 1024:
        parser.error("Configuration file too large")
    values = json.loads(args.path.read_text())
    if not isinstance(values, list):
        parser.error("Expected configuration list")
    policies = [DataPolicy.model_validate({"base_level": "auto", "realtime_enabled": False, **item}) for item in values]
    if len({(p.product, p.symbol) for p in policies}) != len(policies):
        parser.error("Duplicate pair configuration")
    database = create_database(Settings.from_environment())
    try:
        database.initialize()
        lock_path = PROJECT_ROOT / ".local/run/api/subscriptions.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("a") as lease:
            try:
                fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                parser.error("Stop the API and managed services before importing configuration")
            repository = PolicyRepository(database)
            tenant_id = TenantRepository(database).require_default()["id"]
            existing = [DataPolicy.model_validate(item) for item in repository.list(tenant_id=tenant_id)]
            keys = {(p.product, p.symbol) for p in existing}
            added = [p for p in policies if (p.product, p.symbol) not in keys]
            repository.save([*existing, *added], tenant_id=tenant_id)
            print(f"Imported {len(added)} pair configurations; existing parameters and source file preserved.")

    finally:
        database.close()


if __name__ == "__main__":
    main()
