"""Persist opt-in automatic schedules and per-connection backup selections.

Existing backup connections remain selected for both manual and automatic
creation. Automatic creation itself remains disabled until an administrator
enables it.

Revision ID: fd6007599d0b
Revises: 6610b47f2f67
Create Date: 2026-09-03 11:21:56.617459

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "fd6007599d0b"
down_revision: str | Sequence[str] | None = "6610b47f2f67"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("storage_connections", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "manual_backup_enabled",
                sa.Boolean(),
                nullable=False,
                server_default=sa.true(),
            )
        )
        batch_op.add_column(
            sa.Column(
                "automatic_backup_enabled",
                sa.Boolean(),
                nullable=False,
                server_default=sa.true(),
            )
        )

    with op.batch_alter_table("system_config", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "automatic_backups_enabled",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
        batch_op.add_column(
            sa.Column(
                "automatic_backup_time_utc",
                sa.String(length=5),
                nullable=False,
                server_default="02:00",
            )
        )
        batch_op.add_column(
            sa.Column("automatic_backup_last_attempt_at", sa.DateTime(), nullable=True)
        )

def downgrade() -> None:
    with op.batch_alter_table("system_config", schema=None) as batch_op:
        batch_op.drop_column("automatic_backup_last_attempt_at")
        batch_op.drop_column("automatic_backup_time_utc")
        batch_op.drop_column("automatic_backups_enabled")

    with op.batch_alter_table("storage_connections", schema=None) as batch_op:
        batch_op.drop_column("automatic_backup_enabled")
        batch_op.drop_column("manual_backup_enabled")
