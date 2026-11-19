"""User resource grants and inherited CPU/memory/node overrides.

Revision ID: ff19a7c204d1
Revises: f31c9aa26b41
"""
import sqlalchemy as sa
from alembic import op

revision = "ff19a7c204d1"
down_revision = "f31c9aa26b41"
branch_labels = None
depends_on = None


def upgrade():
    identity = sa.BigInteger().with_variant(sa.Integer(), "sqlite")
    op.create_table(
        "user_resource_settings",
        sa.Column("user_id", identity,
                  sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("configuration_json", sa.Text(), nullable=False),
    )


def downgrade():
    op.drop_table("user_resource_settings")
