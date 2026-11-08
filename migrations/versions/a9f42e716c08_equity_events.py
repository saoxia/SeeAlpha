"""Persist valuation evidence, derived points, state and rebuild audit.

Revision ID: a9f42e716c08
Revises: 3debb89b484a
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "a9f42e716c08"
down_revision = "3debb89b484a"
branch_labels = None
depends_on = None


def upgrade():
    identity = sa.BigInteger().with_variant(sa.Integer(), "sqlite")
    document = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
    op.create_table(
        "equity_events",
        sa.Column("id", identity, primary_key=True, autoincrement=True),
        sa.Column("account_id", identity, nullable=False),
        sa.Column("environment", sa.Text(), nullable=False),
        sa.Column("source_id", sa.Text(), nullable=False, unique=True),
        sa.Column("timestamp", sa.BigInteger(), nullable=False),
        sa.Column("body", document, nullable=False),
    )
    op.create_index("equity_events_account_time", "equity_events",
                    ["account_id", "environment", "timestamp", "id"])
    op.create_table("equity_points",
                    sa.Column("event_id", identity, sa.ForeignKey("equity_events.id"), primary_key=True),
                    sa.Column("body", document, nullable=False))
    op.create_table("equity_state",
                    sa.Column("account_id", identity, primary_key=True),
                    sa.Column("environment", sa.Text(), primary_key=True),
                    sa.Column("body", document, nullable=False))
    op.create_table("equity_rebuilds",
                    sa.Column("id", sa.Text(), primary_key=True),
                    sa.Column("account_id", identity, nullable=False),
                    sa.Column("environment", sa.Text(), nullable=False),
                    sa.Column("body", document, nullable=False))


def downgrade():
    raise RuntimeError("资产原始证据和重算审计不能自动删除；请单独评审降级。")
