"""add richer print metadata and filament profiles

Revision ID: d4a7c2e9b6f1
Revises: c2f6a3e9b8d4
Create Date: 2026-06-07 12:00:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "d4a7c2e9b6f1"
down_revision = "c2f6a3e9b8d4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("metadata", sa.Column("first_layer_height_mm", sa.Float(), nullable=True))
    op.add_column("metadata", sa.Column("wall_loops", sa.Integer(), nullable=True))
    op.add_column("metadata", sa.Column("top_shell_layers", sa.Integer(), nullable=True))
    op.add_column("metadata", sa.Column("bottom_shell_layers", sa.Integer(), nullable=True))
    op.add_column("metadata", sa.Column("support_material", sa.Boolean(), nullable=True))
    op.add_column("metadata", sa.Column("nozzle_temperature_c", sa.Float(), nullable=True))
    op.add_column("metadata", sa.Column("bed_temperature_c", sa.Float(), nullable=True))
    op.create_table(
        "filament_profiles",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("material_type", sa.String(length=64), nullable=True),
        sa.Column("material_brand", sa.String(length=128), nullable=True),
        sa.Column("cost_per_kg", sa.Float(), nullable=True),
        sa.Column("notes", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_filament_profiles_name", "filament_profiles", ["name"], unique=True)
    op.create_index(
        "ix_filament_profiles_material_type",
        "filament_profiles",
        ["material_type"],
        unique=False,
    )
    op.create_index(
        "ix_filament_profiles_material_brand",
        "filament_profiles",
        ["material_brand"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_filament_profiles_material_brand", table_name="filament_profiles")
    op.drop_index("ix_filament_profiles_material_type", table_name="filament_profiles")
    op.drop_index("ix_filament_profiles_name", table_name="filament_profiles")
    op.drop_table("filament_profiles")
    op.drop_column("metadata", "bed_temperature_c")
    op.drop_column("metadata", "nozzle_temperature_c")
    op.drop_column("metadata", "support_material")
    op.drop_column("metadata", "bottom_shell_layers")
    op.drop_column("metadata", "top_shell_layers")
    op.drop_column("metadata", "wall_loops")
    op.drop_column("metadata", "first_layer_height_mm")
