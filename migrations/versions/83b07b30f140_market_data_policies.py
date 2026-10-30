"""Store desired market service configuration separately from runtime files.

Revision ID: 83b07b30f140
Revises: 6b632d6e46d8
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "83b07b30f140"
down_revision = "6b632d6e46d8"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "market_data_policies",
        sa.Column("product", sa.Text(), primary_key=True),
        sa.Column("symbol", sa.Text(), primary_key=True),
        sa.Column("parameters", sa.JSON().with_variant(postgresql.JSONB(), "postgresql"), nullable=False),
    )


def downgrade():
    raise RuntimeError("行情服务配置不能自动删除；请从备份恢复。")
