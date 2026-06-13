"""add model source url

Revision ID: 6f4a2c9d8e10
Revises: e8d1c5b3a7f2
Create Date: 2026-06-12 15:20:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "6f4a2c9d8e10"
down_revision = "e8d1c5b3a7f2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("models", sa.Column("source_url", sa.String(length=2048), nullable=True))


def downgrade() -> None:
    op.drop_column("models", "source_url")
