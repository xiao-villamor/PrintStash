"""allow staging leases owned by inbox review items

Revision ID: fc15a6e9b8d4
Revises: fb14d5e8a7c3
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "fc15a6e9b8d4"
down_revision: str | None = "fb14d5e8a7c3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # batch mode keeps the FK/check change portable to SQLite while retaining
    # the existing unnamed UNIQUE(background_job_id) from a1d7.
    with op.batch_alter_table("staging_leases") as batch:
        batch.alter_column(
            "background_job_id",
            existing_type=sa.String(length=64),
            nullable=True,
        )
        batch.add_column(sa.Column("inbox_item_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_staging_leases_inbox_item_id_inbox_items",
            "inbox_items",
            ["inbox_item_id"],
            ["id"],
            ondelete="CASCADE",
        )
        batch.create_unique_constraint(
            "uq_staging_leases_inbox_item_id", ["inbox_item_id"]
        )
        batch.create_index(
            "ix_staging_leases_inbox_item_id", ["inbox_item_id"], unique=False
        )
        batch.create_check_constraint(
            "ck_staging_leases_exactly_one_owner",
            "(background_job_id IS NOT NULL AND inbox_item_id IS NULL) OR "
            "(background_job_id IS NULL AND inbox_item_id IS NOT NULL)",
        )


def downgrade() -> None:
    # An old binary cannot represent review-owned leases. Drop only those
    # records; their paths are deliberately never traversed or removed here.
    op.execute("DELETE FROM staging_leases WHERE background_job_id IS NULL")
    with op.batch_alter_table("staging_leases") as batch:
        batch.drop_constraint("ck_staging_leases_exactly_one_owner", type_="check")
        batch.drop_index("ix_staging_leases_inbox_item_id")
        batch.drop_constraint("uq_staging_leases_inbox_item_id", type_="unique")
        batch.drop_constraint(
            "fk_staging_leases_inbox_item_id_inbox_items", type_="foreignkey"
        )
        batch.drop_column("inbox_item_id")
        batch.alter_column(
            "background_job_id",
            existing_type=sa.String(length=64),
            nullable=False,
        )
