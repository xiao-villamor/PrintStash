"""backfill recommended gcode marker

Revision ID: e8d1c5b3a7f2
Revises: a1e5d8c4f2b7
Create Date: 2026-06-12 12:00:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "e8d1c5b3a7f2"
down_revision = "a1e5d8c4f2b7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Models with live G-code but no recommended revision predate the
    # auto-recommend rule. Promote the latest live G-code per model so the
    # UI no longer has to fall back to "last revision, unflagged".
    op.execute(
        sa.text(
            """
            UPDATE files SET is_recommended = 1
            WHERE id IN (
                SELECT f.id FROM files f
                WHERE f.file_type = 'gcode'
                  AND f.deleted_at IS NULL
                  AND f.version = (
                      SELECT MAX(f2.version) FROM files f2
                      WHERE f2.model_id = f.model_id
                        AND f2.file_type = 'gcode'
                        AND f2.deleted_at IS NULL
                  )
                  AND NOT EXISTS (
                      SELECT 1 FROM files f3
                      WHERE f3.model_id = f.model_id
                        AND f3.file_type = 'gcode'
                        AND f3.is_recommended = 1
                        AND f3.deleted_at IS NULL
                  )
            )
            """
        )
    )


def downgrade() -> None:
    # Data backfill — not reversible (original flags unknown).
    pass
