"""add detected printer profiles

Revision ID: 9d3a5c7e4b12
Revises: 2b61f7e4a9c0
Create Date: 2026-06-08 12:00:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "9d3a5c7e4b12"
down_revision = "2b61f7e4a9c0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "printer_profiles",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("printer_model", sa.String(length=128), nullable=True),
        sa.Column("slicer_name", sa.String(length=64), nullable=True),
        sa.Column("nozzle_diameter_mm", sa.Float(), nullable=True),
        sa.Column("notes", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_printer_profiles_name", "printer_profiles", ["name"], unique=True)
    op.create_index(
        "ix_printer_profiles_printer_model",
        "printer_profiles",
        ["printer_model"],
        unique=False,
    )
    op.create_index(
        "ix_printer_profiles_slicer_name",
        "printer_profiles",
        ["slicer_name"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_printer_profiles_slicer_name", table_name="printer_profiles")
    op.drop_index("ix_printer_profiles_printer_model", table_name="printer_profiles")
    op.drop_index("ix_printer_profiles_name", table_name="printer_profiles")
    op.drop_table("printer_profiles")
