"""add gcode revision fields

Revision ID: 1f9c2e7a4b6d
Revises: 4f0f8e06df71
Create Date: 2026-05-31 00:00:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "1f9c2e7a4b6d"
down_revision = "4f0f8e06df71"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "files",
        sa.Column("revision_status", sa.String(length=32), nullable=True),
    )
    op.add_column("files", sa.Column("revision_notes", sa.Text(), nullable=True))
    op.add_column(
        "files",
        sa.Column(
            "is_recommended",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.create_index(
        "ix_files_revision_status", "files", ["revision_status"], unique=False
    )
    op.create_index(
        "ix_files_is_recommended", "files", ["is_recommended"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_files_is_recommended", table_name="files")
    op.drop_index("ix_files_revision_status", table_name="files")
    op.drop_column("files", "is_recommended")
    op.drop_column("files", "revision_notes")
    op.drop_column("files", "revision_status")
