"""add private representative model source covers

Revision ID: fd16b7f0c9e5
Revises: fc15a6e9b8d4
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "fd16b7f0c9e5"
down_revision: str | None = "fc15a6e9b8d4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "model_source_covers",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("provenance_source_id", sa.Integer(), nullable=False),
        sa.Column("storage_key", sa.String(length=2048), nullable=False),
        sa.Column("content_type", sa.String(length=64), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["provenance_source_id"],
            ["model_provenance_sources.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provenance_source_id"),
        sa.UniqueConstraint("storage_key"),
    )
    op.create_index(
        "ix_model_source_covers_provenance_source_id",
        "model_source_covers",
        ["provenance_source_id"],
    )
    op.create_index(
        "ix_model_source_covers_created_by", "model_source_covers", ["created_by"]
    )
    op.create_index(
        "ix_model_source_covers_updated_at", "model_source_covers", ["updated_at"]
    )
    # SQLite needs batch mode for the changed owner XOR constraint. Existing
    # job/inbox leases are copied untouched while cover-owned leases become a
    # third, independently cascade-deleted owner kind.
    with op.batch_alter_table("staging_leases") as batch:
        batch.drop_constraint("ck_staging_leases_exactly_one_owner", type_="check")
        batch.add_column(
            sa.Column("model_source_cover_id", sa.Integer(), nullable=True)
        )
        batch.create_foreign_key(
            "fk_staging_leases_model_source_cover_id_model_source_covers",
            "model_source_covers",
            ["model_source_cover_id"],
            ["id"],
            ondelete="CASCADE",
        )
        batch.create_unique_constraint(
            "uq_staging_leases_model_source_cover_id", ["model_source_cover_id"]
        )
        batch.create_index(
            "ix_staging_leases_model_source_cover_id",
            ["model_source_cover_id"],
            unique=False,
        )
        batch.create_check_constraint(
            "ck_staging_leases_exactly_one_owner",
            "(background_job_id IS NOT NULL AND inbox_item_id IS NULL AND model_source_cover_id IS NULL) OR "
            "(background_job_id IS NULL AND inbox_item_id IS NOT NULL AND model_source_cover_id IS NULL) OR "
            "(background_job_id IS NULL AND inbox_item_id IS NULL AND model_source_cover_id IS NOT NULL)",
        )


def downgrade() -> None:
    # Downgrading removes only the new owner class. It deliberately never
    # dereferences paths or storage keys.
    op.execute("DELETE FROM staging_leases WHERE model_source_cover_id IS NOT NULL")
    with op.batch_alter_table("staging_leases") as batch:
        batch.drop_constraint("ck_staging_leases_exactly_one_owner", type_="check")
        batch.drop_index("ix_staging_leases_model_source_cover_id")
        batch.drop_constraint("uq_staging_leases_model_source_cover_id", type_="unique")
        batch.drop_constraint(
            "fk_staging_leases_model_source_cover_id_model_source_covers",
            type_="foreignkey",
        )
        batch.drop_column("model_source_cover_id")
        batch.create_check_constraint(
            "ck_staging_leases_exactly_one_owner",
            "(background_job_id IS NOT NULL AND inbox_item_id IS NULL) OR "
            "(background_job_id IS NULL AND inbox_item_id IS NOT NULL)",
        )
    op.drop_table("model_source_covers")
