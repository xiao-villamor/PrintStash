"""preserve external print identity and artifact evidence

Revision ID: c4e7a2b9d1f6
Revises: b3e8d1f6a4c2
Create Date: 2026-08-17 00:00:00
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "c4e7a2b9d1f6"
down_revision: str | None = "b3e8d1f6a4c2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("print_jobs") as batch:
        batch.add_column(sa.Column("external_display_name", sa.String(512)))
        batch.add_column(sa.Column("external_task_id", sa.String(255)))
        batch.add_column(sa.Column("external_subtask_id", sa.String(255)))
        batch.add_column(sa.Column("external_project_id", sa.String(255)))
        batch.add_column(sa.Column("external_profile_id", sa.String(255)))
        batch.add_column(sa.Column("external_gcode_file", sa.String(1024)))
        batch.add_column(sa.Column("external_plate_index", sa.Integer()))
        batch.add_column(sa.Column("external_current_layer", sa.Integer()))
        batch.add_column(sa.Column("external_total_layers", sa.Integer()))
        batch.add_column(sa.Column("external_nozzle_diameter", sa.Float()))
        batch.add_column(
            sa.Column(
                "artifact_evidence",
                sa.String(32),
                nullable=False,
                server_default="vault",
            )
        )
        batch.add_column(sa.Column("artifact_capture_error", sa.String(1024)))
        batch.create_index("ix_print_jobs_external_task_id", ["external_task_id"])
        batch.create_index("ix_print_jobs_artifact_evidence", ["artifact_evidence"])

    op.execute(
        sa.text(
            "UPDATE print_jobs SET artifact_evidence = 'metadata_only' "
            "WHERE source = 'external'"
        )
    )


def downgrade() -> None:
    with op.batch_alter_table("print_jobs") as batch:
        batch.drop_index("ix_print_jobs_artifact_evidence")
        batch.drop_index("ix_print_jobs_external_task_id")
        batch.drop_column("artifact_capture_error")
        batch.drop_column("artifact_evidence")
        batch.drop_column("external_nozzle_diameter")
        batch.drop_column("external_total_layers")
        batch.drop_column("external_current_layer")
        batch.drop_column("external_plate_index")
        batch.drop_column("external_gcode_file")
        batch.drop_column("external_profile_id")
        batch.drop_column("external_project_id")
        batch.drop_column("external_subtask_id")
        batch.drop_column("external_task_id")
        batch.drop_column("external_display_name")
