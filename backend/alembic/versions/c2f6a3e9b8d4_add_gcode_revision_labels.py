"""add gcode revision labels

Revision ID: c2f6a3e9b8d4
Revises: b7c9a8d2e1f4
Create Date: 2026-06-03 12:00:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "c2f6a3e9b8d4"
down_revision = "b7c9a8d2e1f4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "files",
        sa.Column("revision_label", sa.String(length=128), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("files", "revision_label")
