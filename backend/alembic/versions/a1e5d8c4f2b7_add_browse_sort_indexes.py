"""add browse sort indexes

Revision ID: a1e5d8c4f2b7
Revises: f4a2b9c8d7e1
Create Date: 2026-06-10 12:00:00
"""

from __future__ import annotations

from alembic import op


revision = "a1e5d8c4f2b7"
down_revision = "f4a2b9c8d7e1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Library browse sorts on models.updated_at; print summaries sort on
    # files.uploaded_at. Both were unindexed full-table sorts.
    op.create_index("ix_models_updated_at", "models", ["updated_at"])
    op.create_index("ix_files_uploaded_at", "files", ["uploaded_at"])


def downgrade() -> None:
    op.drop_index("ix_files_uploaded_at", table_name="files")
    op.drop_index("ix_models_updated_at", table_name="models")
