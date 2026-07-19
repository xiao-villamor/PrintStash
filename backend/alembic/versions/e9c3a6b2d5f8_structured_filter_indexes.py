"""add measured structured filter indexes

Revision ID: e9c3a6b2d5f8
Revises: d8b2f5a1c4e7
Create Date: 2026-07-19
"""

from collections.abc import Sequence

from alembic import op

revision: str = "e9c3a6b2d5f8"
down_revision: str | None = "d8b2f5a1c4e7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index("ix_metadata_material_type", "metadata", ["material_type"])
    op.create_index("ix_metadata_slicer_name", "metadata", ["slicer_name"])
    op.create_index("ix_metadata_printer_model", "metadata", ["printer_model"])
    op.create_index("ix_print_jobs_model_state", "print_jobs", ["model_id", "state"])


def downgrade() -> None:
    op.drop_index("ix_print_jobs_model_state", table_name="print_jobs")
    op.drop_index("ix_metadata_printer_model", table_name="metadata")
    op.drop_index("ix_metadata_slicer_name", table_name="metadata")
    op.drop_index("ix_metadata_material_type", table_name="metadata")
