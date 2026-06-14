"""add revision scope to share links

Revision ID: ab4f6d9e2c31
Revises: d7f3a9c1e504
Create Date: 2026-06-14 16:00:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "ab4f6d9e2c31"
down_revision = "d7f3a9c1e504"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("share_links", schema=None) as batch_op:
        batch_op.add_column(sa.Column("selected_file_ids_json", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("share_links", schema=None) as batch_op:
        batch_op.drop_column("selected_file_ids_json")
