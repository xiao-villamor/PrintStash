"""add material-aware fleet dispatch

Revision ID: e7b4c1d9a6f2
Revises: c4e7a2b9d1f6
Create Date: 2026-08-19 00:00:00
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "e7b4c1d9a6f2"
down_revision: str | None = "c4e7a2b9d1f6"
branch_labels = None
depends_on = None


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    ]


def upgrade() -> None:
    with op.batch_alter_table("printers") as batch:
        batch.add_column(
            sa.Column(
                "provider_material_sync_enabled",
                sa.Boolean(),
                nullable=False,
                server_default=sa.true(),
            )
        )
        batch.add_column(
            sa.Column(
                "operator_release_required",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
        batch.create_index(
            "ix_printers_operator_release_required", ["operator_release_required"]
        )

    op.create_table(
        "artifact_material_requirements",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("file_id", sa.Integer(), nullable=False),
        sa.Column("tool_index", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("material_type", sa.String(64), nullable=True),
        sa.Column("color_hex", sa.String(16), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(["file_id"], ["files.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "file_id", "tool_index", name="uq_artifact_material_requirement_tool"
        ),
    )
    op.create_index(
        "ix_artifact_material_requirements_file_id",
        "artifact_material_requirements",
        ["file_id"],
    )

    op.create_table(
        "printer_tools",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("printer_id", sa.Integer(), nullable=False),
        sa.Column("tool_key", sa.String(64), nullable=False, server_default="tool0"),
        sa.Column("label", sa.String(128), nullable=False, server_default="Tool 0"),
        sa.Column("nozzle_diameter_mm", sa.Float(), nullable=True),
        sa.Column("source", sa.String(32), nullable=False, server_default="MANUAL"),
        sa.Column("observed_at", sa.DateTime(), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("updated_by", sa.Integer(), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(["printer_id"], ["printers.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "printer_id", "source", "tool_key", name="uq_printer_tools_source_key"
        ),
    )
    op.create_index("ix_printer_tools_printer_id", "printer_tools", ["printer_id"])

    op.create_table(
        "printer_material_slots",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("printer_id", sa.Integer(), nullable=False),
        sa.Column("slot_key", sa.String(64), nullable=False),
        sa.Column("label", sa.String(128), nullable=False),
        sa.Column("tool_key", sa.String(64), nullable=True),
        sa.Column("state", sa.String(16), nullable=False, server_default="UNKNOWN"),
        sa.Column("source", sa.String(32), nullable=False, server_default="MANUAL"),
        sa.Column("material_type", sa.String(64), nullable=True),
        sa.Column("material_brand", sa.String(128), nullable=True),
        sa.Column("color_hex", sa.String(16), nullable=True),
        sa.Column("spool_id", sa.Integer(), nullable=True),
        sa.Column("spool_name", sa.String(256), nullable=True),
        sa.Column("spool_filament_id", sa.Integer(), nullable=True),
        sa.Column("observed_at", sa.DateTime(), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("updated_by", sa.Integer(), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(["printer_id"], ["printers.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "printer_id",
            "source",
            "slot_key",
            name="uq_printer_material_slot_source_key",
        ),
    )
    for column in (
        "printer_id",
        "state",
        "spool_id",
        "spool_filament_id",
        "observed_at",
    ):
        op.create_index(
            f"ix_printer_material_slots_{column}", "printer_material_slots", [column]
        )

    op.create_table(
        "print_batches",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("file_id", sa.Integer(), nullable=False),
        sa.Column("model_id", sa.Integer(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column(
            "routing_strategy",
            sa.String(16),
            nullable=False,
            server_default="LEAST_BUSY",
        ),
        sa.Column("priority", sa.String(16), nullable=False, server_default="NORMAL"),
        sa.Column("target_group", sa.String(128), nullable=True),
        sa.Column(
            "compatibility_policy", sa.String(32), nullable=False, server_default="SAFE"
        ),
        sa.Column("requested_by", sa.Integer(), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("updated_by", sa.Integer(), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(["file_id"], ["files.id"]),
        sa.ForeignKeyConstraint(["model_id"], ["models.id"]),
        sa.ForeignKeyConstraint(["requested_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"]),
        sa.CheckConstraint("quantity > 0", name="ck_print_batches_quantity_positive"),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in ("file_id", "model_id", "target_group", "requested_by"):
        op.create_index(f"ix_print_batches_{column}", "print_batches", [column])

    with op.batch_alter_table("print_jobs") as batch:
        batch.add_column(sa.Column("batch_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("copy_index", sa.Integer(), nullable=True))
        batch.add_column(
            sa.Column(
                "priority", sa.String(16), nullable=False, server_default="NORMAL"
            )
        )
        batch.add_column(sa.Column("target_group", sa.String(128), nullable=True))
        batch.add_column(
            sa.Column(
                "compatibility_policy",
                sa.String(32),
                nullable=False,
                server_default="SAFE",
            )
        )
        batch.add_column(sa.Column("material_override_by", sa.Integer(), nullable=True))
        batch.add_column(
            sa.Column("material_override_at", sa.DateTime(), nullable=True)
        )
        batch.add_column(
            sa.Column(
                "operator_gate_state",
                sa.String(24),
                nullable=False,
                server_default="NOT_REQUIRED",
            )
        )
        batch.add_column(sa.Column("operator_decided_by", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("operator_decided_at", sa.DateTime(), nullable=True))
        batch.create_foreign_key(
            "fk_print_jobs_batch", "print_batches", ["batch_id"], ["id"]
        )
        batch.create_foreign_key(
            "fk_print_jobs_material_override_by",
            "users",
            ["material_override_by"],
            ["id"],
        )
        batch.create_foreign_key(
            "fk_print_jobs_operator_decided_by",
            "users",
            ["operator_decided_by"],
            ["id"],
        )
        batch.create_unique_constraint(
            "uq_print_jobs_batch_copy", ["batch_id", "copy_index"]
        )
        for column in (
            "batch_id",
            "priority",
            "target_group",
            "compatibility_policy",
            "operator_gate_state",
        ):
            batch.create_index(f"ix_print_jobs_{column}", [column])

    op.execute(
        sa.text(
            "INSERT INTO printer_tools "
            "(printer_id, tool_key, label, source, created_at, updated_at) "
            "SELECT id, 'tool0', 'Tool 0', 'MANUAL', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP "
            "FROM printers"
        )
    )
    op.execute(
        sa.text(
            "INSERT INTO artifact_material_requirements "
            "(file_id, tool_index, material_type, created_at, updated_at) "
            "SELECT file_id, 0, material_type, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP "
            "FROM metadata WHERE material_type IS NOT NULL AND TRIM(material_type) <> ''"
        )
    )


def downgrade() -> None:
    with op.batch_alter_table("print_jobs") as batch:
        for column in (
            "operator_gate_state",
            "compatibility_policy",
            "target_group",
            "priority",
            "batch_id",
        ):
            batch.drop_index(f"ix_print_jobs_{column}")
        batch.drop_constraint("fk_print_jobs_operator_decided_by", type_="foreignkey")
        batch.drop_constraint("fk_print_jobs_material_override_by", type_="foreignkey")
        batch.drop_constraint("fk_print_jobs_batch", type_="foreignkey")
        batch.drop_constraint("uq_print_jobs_batch_copy", type_="unique")
        for column in (
            "operator_decided_at",
            "operator_decided_by",
            "operator_gate_state",
            "material_override_at",
            "material_override_by",
            "compatibility_policy",
            "target_group",
            "priority",
            "copy_index",
            "batch_id",
        ):
            batch.drop_column(column)

    op.drop_table("print_batches")
    op.drop_table("printer_material_slots")
    op.drop_table("printer_tools")
    op.drop_table("artifact_material_requirements")
    with op.batch_alter_table("printers") as batch:
        batch.drop_index("ix_printers_operator_release_required")
        batch.drop_column("operator_release_required")
        batch.drop_column("provider_material_sync_enabled")
