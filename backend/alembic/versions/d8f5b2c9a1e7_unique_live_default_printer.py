"""unique live default printer

Revision ID: d8f5b2c9a1e7
Revises: c7e4a1b9d2f6
Create Date: 2026-08-12
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d8f5b2c9a1e7"
down_revision: str | None = "c7e4a1b9d2f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    printers = sa.table(
        "printers",
        sa.column("id", sa.Integer()),
        sa.column("is_default", sa.Boolean()),
        sa.column("deleted_at", sa.DateTime()),
    )
    keeper = (
        sa.select(sa.func.min(printers.c.id))
        .where(
            printers.c.is_default.is_(True),
            printers.c.deleted_at.is_(None),
        )
        .scalar_subquery()
    )
    op.execute(
        sa.update(printers)
        .where(
            printers.c.is_default.is_(True),
            printers.c.deleted_at.is_(None),
            printers.c.id != keeper,
        )
        .values(is_default=False)
    )
    op.create_index(
        "uq_printers_live_default",
        "printers",
        ["is_default"],
        unique=True,
        sqlite_where=sa.text("is_default = 1 AND deleted_at IS NULL"),
        postgresql_where=sa.text("is_default IS TRUE AND deleted_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_printers_live_default", table_name="printers")
