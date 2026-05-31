"""add printer file inventory

Revision ID: b7c9a8d2e1f4
Revises: 1f9c2e7a4b6d
Create Date: 2026-05-31 21:15:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "b7c9a8d2e1f4"
down_revision = "1f9c2e7a4b6d"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "printer_files",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("printer_id", sa.Integer(), nullable=False),
        sa.Column("file_id", sa.Integer(), nullable=True),
        sa.Column("remote_filename", sa.String(length=512), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=True),
        sa.Column("sha256", sa.String(length=64), nullable=True),
        sa.Column("matched_by", sa.String(length=32), nullable=False, server_default="external"),
        sa.Column("modified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("missing_since", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["printer_id"], ["printers.id"]),
        sa.ForeignKeyConstraint(["file_id"], ["files.id"]),
        sa.UniqueConstraint("printer_id", "remote_filename", name="uq_printer_files_printer_remote"),
    )
    op.create_index("ix_printer_files_printer_id", "printer_files", ["printer_id"])
    op.create_index("ix_printer_files_file_id", "printer_files", ["file_id"])
    op.create_index("ix_printer_files_sha256", "printer_files", ["sha256"])
    op.create_index("ix_printer_files_matched_by", "printer_files", ["matched_by"])
    op.create_index("ix_printer_files_last_seen_at", "printer_files", ["last_seen_at"])
    op.create_index("ix_printer_files_missing_since", "printer_files", ["missing_since"])


def downgrade() -> None:
    op.drop_index("ix_printer_files_missing_since", table_name="printer_files")
    op.drop_index("ix_printer_files_last_seen_at", table_name="printer_files")
    op.drop_index("ix_printer_files_matched_by", table_name="printer_files")
    op.drop_index("ix_printer_files_sha256", table_name="printer_files")
    op.drop_index("ix_printer_files_file_id", table_name="printer_files")
    op.drop_index("ix_printer_files_printer_id", table_name="printer_files")
    op.drop_table("printer_files")
