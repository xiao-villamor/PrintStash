"""generalize owned storage objects into a publication intent ledger

Revision ID: b9d3e7f1a5c2
Revises: a7c9e1b5d3f2
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "b9d3e7f1a5c2"
down_revision: str | None = "a7c9e1b5d3f2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("owned_storage_objects") as batch:
        batch.add_column(
            sa.Column(
                "state",
                sa.String(length=16),
                nullable=False,
                server_default="committed",
            )
        )
        batch.add_column(sa.Column("sha256", sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("committed_at", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("last_error", sa.String(length=255), nullable=True))
        batch.alter_column("token", existing_type=sa.String(length=64), nullable=True)
        batch.alter_column("size_bytes", existing_type=sa.Integer(), nullable=True)
        batch.create_index("ix_owned_storage_objects_state", ["state"], unique=False)
        batch.create_index("ix_owned_storage_objects_sha256", ["sha256"], unique=False)
        batch.create_index(
            "ix_owned_storage_objects_created_at", ["created_at"], unique=False
        )
        batch.create_index(
            "ix_owned_storage_objects_committed_at", ["committed_at"], unique=False
        )
    op.execute(
        sa.text(
            "UPDATE owned_storage_objects SET committed_at = created_at "
            "WHERE state = 'committed'"
        )
    )


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM owned_storage_objects WHERE state != 'committed'"))
    with op.batch_alter_table("owned_storage_objects") as batch:
        batch.drop_index("ix_owned_storage_objects_committed_at")
        batch.drop_index("ix_owned_storage_objects_created_at")
        batch.drop_index("ix_owned_storage_objects_sha256")
        batch.drop_index("ix_owned_storage_objects_state")
        batch.alter_column("size_bytes", existing_type=sa.Integer(), nullable=False)
        batch.alter_column("token", existing_type=sa.String(length=64), nullable=False)
        batch.drop_column("last_error")
        batch.drop_column("committed_at")
        batch.drop_column("sha256")
        batch.drop_column("state")
