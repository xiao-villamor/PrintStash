"""Add typed storage-provider configuration.

Revision ID: c4e8a2f6b1d9
Revises: b9d3e7f1a5c2
"""

import sqlalchemy as sa

from alembic import op

revision = "c4e8a2f6b1d9"
down_revision = "b9d3e7f1a5c2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("system_config") as batch:
        batch.add_column(sa.Column("storage_provider", sa.String(64), nullable=True))
        batch.add_column(
            sa.Column("storage_provider_config_json", sa.Text(), nullable=True)
        )
        batch.add_column(
            sa.Column("storage_provider_secret_json", sa.Text(), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("system_config") as batch:
        batch.drop_column("storage_provider_secret_json")
        batch.drop_column("storage_provider_config_json")
        batch.drop_column("storage_provider")
