"""Upgrade the previously unmodeled ledger check without weakening or rewriting evidence."""

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations as MigrationOperations
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from api.db.orm import ORMDatabase
from api.operations.orm_repository import ORMOperations
from tests.api.operations_test import cash_event, event

OLD_EVENTS = "'place','amend','cancel','fill','reject','expire','correction'"


def configured_database(tmp_path: Path):
    url = f"sqlite:///{tmp_path / 'ledger.db'}"
    config = Config("alembic.ini")
    config.attributes["database_url"] = url
    command.upgrade(config, "83b07b30f140")
    return config, url


def legacy_check(url, expression):
    engine = create_engine(url)
    with engine.begin() as connection:
        operations = MigrationOperations(MigrationContext.configure(connection))
        with operations.batch_alter_table("ledger_operations") as batch:
            batch.create_check_constraint("ledger_operations_event_type_check", expression)
    engine.dispose()


def test_upgrade_preserves_old_evidence_and_retains_type_validation(tmp_path):
    config, url = configured_database(tmp_path)
    legacy_check(url, f"event_type in ({OLD_EVENTS})")
    database = ORMDatabase(url, tmp_path / "ledger.db")
    repository = ORMOperations(database)
    original = repository.ingest(event())
    before = repository.detail(original["operation_id"])
    command.upgrade(config, "head")
    database.initialize()
    assert repository.detail(original["operation_id"]) == before
    assert repository.ingest(cash_event())["created"]
    assert not repository.ingest(cash_event())["created"]
    with database.engine.connect() as connection:
        checks = inspect(connection).get_check_constraints("ledger_operations")
        assert any("cashflow" in check["sqltext"] for check in checks)
        with pytest.raises(IntegrityError):
            connection.execute(text("UPDATE ledger_operations SET event_type='fabricated'"))
        connection.rollback()
    assert len(repository.list()["records"]) == 2
    database.close()


def test_upgrade_adds_the_constraint_to_old_databases_without_one(tmp_path):
    config, url = configured_database(tmp_path)
    command.upgrade(config, "head")
    database = ORMDatabase(url, tmp_path / "ledger.db")
    database.initialize()
    assert ORMOperations(database).ingest(cash_event())["created"]
    database.close()


@pytest.mark.parametrize("expression", [
    f"event_type in ({OLD_EVENTS},'custom')",
    f"event_type in ({OLD_EVENTS}) AND timestamp > 0",
])
def test_unrecognized_legacy_constraint_is_not_replaced(tmp_path, expression):
    config, url = configured_database(tmp_path)
    legacy_check(url, expression)
    with pytest.raises(RuntimeError, match="先人工核对"):
        command.upgrade(config, "head")
    engine = create_engine(url)
    with engine.connect() as connection:
        checks = inspect(connection).get_check_constraints("ledger_operations")
        assert any(check["sqltext"] == expression for check in checks)
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "83b07b30f140"
    engine.dispose()
