"""Persist management task records and the backtest report index.

Revision ID: 6b632d6e46d8
Revises: 1226686a04d7
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "6b632d6e46d8"
down_revision = "1226686a04d7"
branch_labels = None
depends_on = None


def upgrade():
    document = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
    op.create_table(
        "management_jobs",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("payload", document, nullable=False),
    )
    op.create_index("ix_management_jobs_created_at", "management_jobs", ["created_at"])
    op.create_table(
        "backtest_reports",
        sa.Column("url", sa.Text(), primary_key=True),
        sa.Column("modified_at", sa.Text(), nullable=False),
        sa.Column("payload", document, nullable=False),
    )
    op.create_index("ix_backtest_reports_modified_at", "backtest_reports", ["modified_at"])


def downgrade():
    raise RuntimeError("管理数据不能自动删除；请从备份恢复并按数据迁移流程处理。")
