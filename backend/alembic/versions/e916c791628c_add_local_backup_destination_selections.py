"""Persist independent local destinations for manual and automatic backups.

Existing installations keep local backup publication enabled for both modes;
operators can opt into remote-only backups after upgrading.

Revision ID: e916c791628c
Revises: fd6007599d0b
Create Date: 2026-09-03 13:27:48.833568

"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e916c791628c"
down_revision: Union[str, Sequence[str], None] = "fd6007599d0b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("system_config", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "manual_local_backup_enabled",
                sa.Boolean(),
                server_default="1",
                nullable=False,
            )
        )
        batch_op.add_column(
            sa.Column(
                "automatic_local_backup_enabled",
                sa.Boolean(),
                server_default="1",
                nullable=False,
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("system_config", schema=None) as batch_op:
        batch_op.drop_column("automatic_local_backup_enabled")
        batch_op.drop_column("manual_local_backup_enabled")
