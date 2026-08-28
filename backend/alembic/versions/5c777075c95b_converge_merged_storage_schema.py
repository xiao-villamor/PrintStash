"""converge merged storage schema

Revision ID: 5c777075c95b
Revises: 8b9aaacfed6b
Create Date: 2026-08-28 15:30:09.309147

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "5c777075c95b"
down_revision: str | Sequence[str] | None = "8b9aaacfed6b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("owned_storage_objects") as batch:
        batch.alter_column(
            "state",
            existing_type=sa.String(length=16),
            existing_nullable=False,
            server_default=None,
        )
    with op.batch_alter_table("system_config") as batch:
        batch.alter_column(
            "storage_provider_config_json",
            existing_type=sa.Text(),
            type_=sa.String(),
            existing_nullable=True,
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("system_config") as batch:
        batch.alter_column(
            "storage_provider_config_json",
            existing_type=sa.String(),
            type_=sa.Text(),
            existing_nullable=True,
        )
    with op.batch_alter_table("owned_storage_objects") as batch:
        batch.alter_column(
            "state",
            existing_type=sa.String(length=16),
            existing_nullable=False,
            server_default="PENDING",
        )
