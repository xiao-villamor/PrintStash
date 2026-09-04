"""capture provenance foundation

Revision ID: fa13c4e7b9d2
Revises: e7b4c1d9a6f2
Create Date: 2026-08-24
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "fa13c4e7b9d2"
down_revision = "e7b4c1d9a6f2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "model_provenance_sources",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("model_id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("source_item_id", sa.String(length=255), nullable=True),
        sa.Column("canonical_url", sa.String(length=2048), nullable=False),
        sa.Column("identity_key", sa.String(length=64), nullable=False),
        sa.Column("source_revision", sa.String(length=255), nullable=True),
        sa.Column("first_captured_at", sa.DateTime(), nullable=False),
        sa.Column("last_checked_at", sa.DateTime(), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["model_id"], ["models.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "model_id", "identity_key", name="uq_provenance_source_identity"
        ),
    )
    op.create_index(
        "ix_model_provenance_sources_model_id", "model_provenance_sources", ["model_id"]
    )
    op.create_index(
        "ix_model_provenance_sources_provider", "model_provenance_sources", ["provider"]
    )
    op.create_index(
        "ix_model_provenance_sources_identity_key",
        "model_provenance_sources",
        ["identity_key"],
    )
    op.create_index(
        "ix_model_provenance_sources_last_checked_at",
        "model_provenance_sources",
        ["last_checked_at"],
    )
    op.create_index(
        "ix_model_provenance_sources_updated_at",
        "model_provenance_sources",
        ["updated_at"],
    )
    op.create_index(
        "ix_provenance_source_provider_item",
        "model_provenance_sources",
        ["provider", "source_item_id"],
    )

    op.create_table(
        "model_provenance_fields",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("provenance_source_id", sa.Integer(), nullable=False),
        sa.Column("field_name", sa.String(length=64), nullable=False),
        sa.Column("captured_value_json", sa.Text(), nullable=False),
        sa.Column("captured_origin", sa.String(length=16), nullable=False),
        sa.Column("user_value_json", sa.Text(), nullable=True),
        sa.Column(
            "user_override_set", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("user_updated_by", sa.Integer(), nullable=True),
        sa.Column("captured_at", sa.DateTime(), nullable=True),
        sa.Column("user_updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["provenance_source_id"],
            ["model_provenance_sources.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["user_updated_by"], ["users.id"], ondelete="SET NULL"),
        sa.CheckConstraint(
            "captured_origin IN ('confirmed', 'inferred')",
            name="ck_provenance_field_origin",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "provenance_source_id", "field_name", name="uq_provenance_field_name"
        ),
    )
    op.create_index(
        "ix_model_provenance_fields_provenance_source_id",
        "model_provenance_fields",
        ["provenance_source_id"],
    )

    op.create_table(
        "provenance_captures",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("provenance_source_id", sa.Integer(), nullable=False),
        sa.Column("inbox_item_id", sa.Integer(), nullable=True),
        sa.Column("captured_by", sa.Integer(), nullable=True),
        sa.Column("adapter_version", sa.String(length=64), nullable=False),
        sa.Column("source_revision", sa.String(length=255), nullable=True),
        sa.Column("snapshot_json", sa.Text(), nullable=False),
        sa.Column("snapshot_sha256", sa.String(length=64), nullable=False),
        sa.Column("captured_at", sa.DateTime(), nullable=False),
        sa.Column("checked_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["provenance_source_id"],
            ["model_provenance_sources.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["inbox_item_id"], ["inbox_items.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["captured_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "provenance_source_id",
            "snapshot_sha256",
            name="uq_provenance_capture_snapshot",
        ),
    )
    for column in ("provenance_source_id", "inbox_item_id", "snapshot_sha256"):
        op.create_index(
            f"ix_provenance_captures_{column}", "provenance_captures", [column]
        )

    op.create_table(
        "artifact_provenance_links",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("file_id", sa.Integer(), nullable=False),
        sa.Column("provenance_source_id", sa.Integer(), nullable=False),
        sa.Column("capture_id", sa.Integer(), nullable=True),
        sa.Column("source_file_id", sa.String(length=255), nullable=True),
        sa.Column("source_filename", sa.String(length=512), nullable=False),
        sa.Column("container_entry_path", sa.String(length=1024), nullable=True),
        sa.Column("source_revision", sa.String(length=255), nullable=True),
        sa.Column("blob_sha256", sa.String(length=64), nullable=False),
        sa.Column("import_key", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["file_id"], ["files.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["provenance_source_id"],
            ["model_provenance_sources.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["capture_id"], ["provenance_captures.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("import_key"),
    )
    for column in (
        "file_id",
        "provenance_source_id",
        "blob_sha256",
        "import_key",
        "created_at",
    ):
        op.create_index(
            f"ix_artifact_provenance_links_{column}",
            "artifact_provenance_links",
            [column],
        )

    op.create_table(
        "inbox_item_results",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("inbox_item_id", sa.Integer(), nullable=False),
        sa.Column("source_selection_id", sa.String(length=512), nullable=False),
        sa.Column("result_key", sa.String(length=64), nullable=False),
        sa.Column("original_filename", sa.String(length=512), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("model_id", sa.Integer(), nullable=True),
        sa.Column("file_id", sa.Integer(), nullable=True),
        sa.Column("provenance_source_id", sa.Integer(), nullable=True),
        sa.Column("error_code", sa.String(length=128), nullable=True),
        sa.Column("retryable", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["inbox_item_id"], ["inbox_items.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["model_id"], ["models.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["file_id"], ["files.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["provenance_source_id"],
            ["model_provenance_sources.id"],
            ondelete="SET NULL",
        ),
        sa.CheckConstraint(
            "state IN ('imported', 'deduplicated', 'failed')",
            name="ck_inbox_result_state",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "inbox_item_id",
            "source_selection_id",
            "result_key",
            name="uq_inbox_result_key",
        ),
    )
    for column in (
        "inbox_item_id",
        "state",
        "model_id",
        "file_id",
        "provenance_source_id",
    ):
        op.create_index(
            f"ix_inbox_item_results_{column}", "inbox_item_results", [column]
        )

    op.add_column(
        "inbox_items", sa.Column("completion", sa.String(length=16), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("inbox_items", "completion")
    op.drop_table("inbox_item_results")
    op.drop_table("artifact_provenance_links")
    op.drop_table("provenance_captures")
    op.drop_table("model_provenance_fields")
    op.drop_table("model_provenance_sources")
