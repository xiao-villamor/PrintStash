"""add trash retention config

Revision ID: f4a2b9c8d7e1
Revises: 9d3a5c7e4b12
Create Date: 2026-06-08 16:15:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "f4a2b9c8d7e1"
down_revision = "9d3a5c7e4b12"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("system_config", sa.Column("trash_retention_days", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("system_config", "trash_retention_days")
