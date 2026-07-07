"""add metadata render_status

Revision ID: a9c2e6f1b4d8
Revises: f6b3d0a8c2e9
Create Date: 2026-07-02 12:00:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "a9c2e6f1b4d8"
down_revision = "f6b3d0a8c2e9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "metadata", sa.Column("render_status", sa.String(length=32), nullable=True)
    )
    op.create_index(
        "ix_metadata_render_status", "metadata", ["render_status"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_metadata_render_status", table_name="metadata")
    op.drop_column("metadata", "render_status")
