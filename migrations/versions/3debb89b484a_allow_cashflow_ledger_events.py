"""Allow cashflow evidence without removing existing ledger protections.

Revision ID: 3debb89b484a
Revises: 83b07b30f140
Create Date: 2026-10-31 02:50:03.300471

"""
import re

import sqlalchemy as sa
from alembic import op

revision = "3debb89b484a"
down_revision = "83b07b30f140"
branch_labels = None
depends_on = None

CONSTRAINT = "ledger_operations_event_type_check"
EVENTS = ("place", "amend", "cancel", "fill", "reject", "expire", "correction", "cashflow")


def upgrade() -> None:
    connection = op.get_bind()
    schema = op.get_context().opts.get("version_table_schema")
    if connection.dialect.name == "postgresql":
        connection.execute(sa.text("SET LOCAL lock_timeout = '5s'"))
        connection.execute(sa.text("SET LOCAL statement_timeout = '30s'"))
    checks = [item for item in sa.inspect(connection).get_check_constraints(
        "ledger_operations", schema=schema
    ) if "event_type" in item["sqltext"]]
    if checks:
        if len(checks) != 1 or checks[0]["name"] != CONSTRAINT:
            raise RuntimeError("存在未识别的事件类型约束，请先人工核对；未移除原约束。")
        values = set(re.findall(r"'([^']*)'", checks[0]["sqltext"]))
        shape = re.sub(r"'[^']*'", "?", checks[0]["sqltext"]).lower()
        shape = re.sub(r"::(?:text|character varying)", "", shape)
        shape = re.sub(r"[\s()]", "", shape).replace('"event_type"', "event_type")
        slots = ",".join("?" for _ in values)
        if shape not in {f"event_typein{slots}", f"event_type=anyarray[{slots}]"}:
            raise RuntimeError("事件约束包含未识别条件，请先人工核对；未修改原约束。")
        if values == set(EVENTS):
            return
        if values != set(EVENTS) - {"cashflow"}:
            raise RuntimeError("事件类型约束与已核验版本不同，请先人工核对；未修改原约束。")
    expression = "event_type in (" + ",".join(repr(item) for item in EVENTS) + ")"
    with op.batch_alter_table("ledger_operations", schema=schema) as batch:
        if checks:
            batch.drop_constraint(CONSTRAINT, type_="check")
        batch.create_check_constraint(CONSTRAINT, expression)


def downgrade() -> None:
    raise RuntimeError("资金证据不能自动移除或改写；请在确认已有记录后单独评审降级。")
