"""add durable capture upload slots

Revision ID: fe17c8d1a0f2
Revises: fd16b7f0c9e5
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "fe17c8d1a0f2"
down_revision: str | None = "fd16b7f0c9e5"
branch_labels = None
depends_on = None


def _staging_background_job_unique_name(bind) -> str | None:
    """Find the pre-capture job-owner unique constraint by its columns.

    The published ``a1d7`` migration declared this constraint without a name.
    PostgreSQL therefore assigned ``staging_leases_background_job_id_key``;
    SQLite reflects it as unnamed, then Alembic's batch naming convention
    supplies the conventional name during table recreation.
    """
    try:
        constraints = sa.inspect(bind).get_unique_constraints("staging_leases")
    except sa.exc.NoInspectionAvailable:
        # Offline Alembic rendering cannot reflect the live table. These are
        # the names produced by the two supported dialects for the old
        # unnamed constraint.
        return (
            "staging_leases_background_job_id_key"
            if bind.dialect.name == "postgresql"
            else "uq_staging_leases_background_job_id"
        )
    for constraint in constraints:
        if constraint.get("column_names") == ["background_job_id"]:
            return constraint.get("name") or "uq_staging_leases_background_job_id"
    return None


def upgrade() -> None:
    background_job_unique_name = _staging_background_job_unique_name(op.get_bind())
    with op.batch_alter_table("model_provenance_sources") as batch:
        batch.add_column(
            sa.Column("tags_json", sa.Text(), nullable=False, server_default="[]")
        )
    op.create_table(
        "capture_upload_slots",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("inbox_item_id", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("source_file_id", sa.String(255), nullable=True),
        sa.Column("filename", sa.String(512), nullable=False),
        sa.Column("media_type", sa.String(128), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("storage_key", sa.String(2048), nullable=True, unique=True),
        sa.Column("receipt_json", sa.Text(), nullable=True),
        sa.Column("uploaded_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["inbox_item_id"], ["inbox_items.id"], ondelete="CASCADE"
        ),
    )
    for name, columns in (
        ("ix_capture_upload_slots_inbox_item_id", ["inbox_item_id"]),
        ("ix_capture_upload_slots_role", ["role"]),
        ("ix_capture_upload_slots_sha256", ["sha256"]),
        ("ix_capture_upload_slots_state", ["state"]),
        ("ix_capture_upload_slots_uploaded_at", ["uploaded_at"]),
        ("ix_capture_upload_slots_created_at", ["created_at"]),
        ("ix_capture_upload_slots_updated_at", ["updated_at"]),
    ):
        op.create_index(name, "capture_upload_slots", columns)
    with op.batch_alter_table(
        "staging_leases",
        naming_convention={"uq": "uq_%(table_name)s_%(column_0_name)s"},
    ) as batch:
        batch.drop_constraint("ck_staging_leases_exactly_one_owner", type_="check")
        if background_job_unique_name is not None:
            batch.drop_constraint(background_job_unique_name, type_="unique")
        batch.add_column(
            sa.Column("capture_upload_slot_id", sa.String(64), nullable=True)
        )
        batch.add_column(
            sa.Column("capture_upload_slot_origin_id", sa.String(64), nullable=True)
        )
        batch.create_foreign_key(
            "fk_staging_leases_capture_upload_slot_id_capture_upload_slots",
            "capture_upload_slots",
            ["capture_upload_slot_id"],
            ["id"],
            ondelete="CASCADE",
        )
        batch.create_unique_constraint(
            "uq_staging_leases_capture_upload_slot_id", ["capture_upload_slot_id"]
        )
        batch.create_index(
            "ix_staging_leases_capture_upload_slot_id", ["capture_upload_slot_id"]
        )
        batch.create_index(
            "ix_staging_leases_capture_upload_slot_origin_id",
            ["capture_upload_slot_origin_id"],
        )
        batch.create_check_constraint(
            "ck_staging_leases_exactly_one_owner",
            "(background_job_id IS NOT NULL AND inbox_item_id IS NULL AND model_source_cover_id IS NULL AND capture_upload_slot_id IS NULL) OR "
            "(background_job_id IS NULL AND inbox_item_id IS NOT NULL AND model_source_cover_id IS NULL AND capture_upload_slot_id IS NULL) OR "
            "(background_job_id IS NULL AND inbox_item_id IS NULL AND model_source_cover_id IS NOT NULL AND capture_upload_slot_id IS NULL) OR "
            "(background_job_id IS NULL AND inbox_item_id IS NULL AND model_source_cover_id IS NULL AND capture_upload_slot_id IS NOT NULL)",
        )


def downgrade() -> None:
    # The slot owner is allowed to move to a background job while a capture is
    # importing.  In that state ``capture_upload_slot_id`` is NULL and the
    # durable origin is carried by ``capture_upload_slot_origin_id`` instead.
    # Both forms are capture-owned data that an older schema cannot represent.
    # Remove them before restoring UNIQUE(background_job_id): a multi-file
    # capture may have several origin leases on the same job.  This predicate
    # deliberately leaves pre-existing, non-slot job leases in place.
    op.execute(
        "DELETE FROM staging_leases "
        "WHERE capture_upload_slot_id IS NOT NULL "
        "OR capture_upload_slot_origin_id IS NOT NULL"
    )
    with op.batch_alter_table(
        "staging_leases",
        naming_convention={"uq": "uq_%(table_name)s_%(column_0_name)s"},
    ) as batch:
        batch.drop_constraint("ck_staging_leases_exactly_one_owner", type_="check")
        batch.drop_index("ix_staging_leases_capture_upload_slot_id")
        batch.drop_index("ix_staging_leases_capture_upload_slot_origin_id")
        batch.drop_constraint(
            "uq_staging_leases_capture_upload_slot_id", type_="unique"
        )
        batch.drop_constraint(
            "fk_staging_leases_capture_upload_slot_id_capture_upload_slots",
            type_="foreignkey",
        )
        batch.drop_column("capture_upload_slot_id")
        batch.drop_column("capture_upload_slot_origin_id")
        batch.create_unique_constraint(
            "uq_staging_leases_background_job_id", ["background_job_id"]
        )
        batch.create_check_constraint(
            "ck_staging_leases_exactly_one_owner",
            "(background_job_id IS NOT NULL AND inbox_item_id IS NULL AND model_source_cover_id IS NULL) OR "
            "(background_job_id IS NULL AND inbox_item_id IS NOT NULL AND model_source_cover_id IS NULL) OR "
            "(background_job_id IS NULL AND inbox_item_id IS NULL AND model_source_cover_id IS NOT NULL)",
        )
    op.drop_table("capture_upload_slots")
    with op.batch_alter_table("model_provenance_sources") as batch:
        batch.drop_column("tags_json")
