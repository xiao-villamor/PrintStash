"""serialize artifact revision allocation

Revision ID: b3c8e1f4a6d2
Revises: a2f7c9d4e6b1
Create Date: 2026-08-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b3c8e1f4a6d2"
down_revision: str | None = "a2f7c9d4e6b1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "models",
        sa.Column(
            "next_file_version",
            sa.Integer(),
            server_default=sa.text("1"),
            nullable=False,
        ),
    )

    # A raced installation may already contain duplicate versions. Preserve
    # the oldest row's number and move every duplicate above that Model's
    # previous maximum before the unique index is installed.
    op.execute(
        sa.text(
            """
            WITH ranked AS (
                SELECT
                    id,
                    model_id,
                    version,
                    ROW_NUMBER() OVER (
                        PARTITION BY model_id, version ORDER BY id
                    ) AS duplicate_rank,
                    MAX(version) OVER (PARTITION BY model_id) AS max_version
                FROM files
            ),
            duplicates AS (
                SELECT id, model_id, version, max_version
                FROM ranked
                WHERE duplicate_rank > 1
            ),
            renumbered AS (
                SELECT
                    id,
                    max_version + ROW_NUMBER() OVER (
                        PARTITION BY model_id ORDER BY version, id
                    ) AS new_version
                FROM duplicates
            )
            UPDATE files
            SET version = (
                SELECT new_version FROM renumbered WHERE renumbered.id = files.id
            )
            WHERE id IN (SELECT id FROM renumbered)
            """
        )
    )

    # Keep the newest recommended live G-code for each Model and clear any
    # duplicate markers left by the same race.
    op.execute(
        sa.text(
            """
            WITH recommended AS (
                SELECT
                    id,
                    ROW_NUMBER() OVER (
                        PARTITION BY model_id ORDER BY version DESC, id DESC
                    ) AS recommendation_rank
                FROM files
                WHERE file_type = 'GCODE'
                  AND is_recommended IS TRUE
                  AND deleted_at IS NULL
            )
            UPDATE files
            SET is_recommended = 0
            WHERE id IN (
                SELECT id FROM recommended WHERE recommendation_rank > 1
            )
            """
        )
    )

    op.execute(
        sa.text(
            """
            UPDATE models
            SET next_file_version = COALESCE(
                (SELECT MAX(files.version) + 1
                 FROM files
                 WHERE files.model_id = models.id),
                1
            )
            """
        )
    )

    op.create_index(
        "uq_files_model_version",
        "files",
        ["model_id", "version"],
        unique=True,
    )
    bind = op.get_bind()
    predicate = (
        "file_type = 'GCODE' AND is_recommended IS TRUE AND deleted_at IS NULL"
        if bind.dialect.name == "postgresql"
        else "file_type = 'GCODE' AND is_recommended = 1 AND deleted_at IS NULL"
    )
    op.create_index(
        "uq_files_live_recommended_gcode",
        "files",
        ["model_id"],
        unique=True,
        postgresql_where=sa.text(predicate) if bind.dialect.name == "postgresql" else None,
        sqlite_where=sa.text(predicate) if bind.dialect.name == "sqlite" else None,
    )


def downgrade() -> None:
    op.drop_index("uq_files_live_recommended_gcode", table_name="files")
    op.drop_index("uq_files_model_version", table_name="files")
    op.drop_column("models", "next_file_version")
