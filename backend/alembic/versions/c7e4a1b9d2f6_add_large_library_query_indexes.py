"""add large library query indexes

Revision ID: c7e4a1b9d2f6
Revises: b3c8e1f4a6d2
Create Date: 2026-08-12
"""

from collections.abc import Sequence

from alembic import op

revision: str = "c7e4a1b9d2f6"
down_revision: str | None = "b3c8e1f4a6d2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_files_model_deleted_type",
        "files",
        ["model_id", "deleted_at", "file_type"],
    )
    op.create_index(
        "ix_models_deleted_updated_id",
        "models",
        ["deleted_at", "updated_at", "id"],
    )
    op.create_index(
        "ix_background_jobs_visible_state_owner_updated",
        "background_jobs",
        ["visible", "state", "owner_user_id", "updated_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_background_jobs_visible_state_owner_updated",
        table_name="background_jobs",
    )
    op.drop_index("ix_models_deleted_updated_id", table_name="models")
    op.drop_index("ix_files_model_deleted_type", table_name="files")
