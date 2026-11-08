"""Store the extractable archive, entry point, and hash on a package version.

Revision ID: d8a41c6e2b17
Revises: c4e8a1b27d09
"""

import sqlalchemy as sa
from alembic import op

revision = "d8a41c6e2b17"
down_revision = "c4e8a1b27d09"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("strategy_package_versions", sa.Column("entry_point", sa.Text(), nullable=True))
    op.add_column("strategy_package_versions", sa.Column("package_hash", sa.Text(), nullable=True))
    op.add_column("strategy_package_versions", sa.Column("archive", sa.LargeBinary(), nullable=True))


def downgrade():
    op.drop_column("strategy_package_versions", "archive")
    op.drop_column("strategy_package_versions", "package_hash")
    op.drop_column("strategy_package_versions", "entry_point")
