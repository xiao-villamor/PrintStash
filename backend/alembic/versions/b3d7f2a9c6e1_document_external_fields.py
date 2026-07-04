"""add document external library fields

Revision ID: b3d7f2a9c6e1
Revises: a9c2e6f1b4d8
Create Date: 2026-07-03 10:00:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "b3d7f2a9c6e1"
down_revision = "a9c2e6f1b4d8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "documents",
        sa.Column("is_external", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "documents", sa.Column("external_library_id", sa.Integer(), nullable=True)
    )
    op.add_column(
        "documents", sa.Column("source_path", sa.String(length=1024), nullable=True)
    )
    op.add_column("documents", sa.Column("source_mtime", sa.Float(), nullable=True))
    op.create_index(
        "ix_documents_is_external", "documents", ["is_external"], unique=False
    )
    op.create_index(
        "ix_documents_external_library_id",
        "documents",
        ["external_library_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_documents_external_library_id", table_name="documents")
    op.drop_index("ix_documents_is_external", table_name="documents")
    op.drop_column("documents", "source_mtime")
    op.drop_column("documents", "source_path")
    op.drop_column("documents", "external_library_id")
    op.drop_column("documents", "is_external")
