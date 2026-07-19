"""add durable pending imports

Revision ID: d8b2f5a1c4e7
Revises: c7a1e4d9b2f6
Create Date: 2026-07-19
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d8b2f5a1c4e7"
down_revision: str | None = "c7a1e4d9b2f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "inbox_items",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("owner_user_id", sa.Integer(), nullable=False),
        sa.Column("source_kind", sa.String(length=16), nullable=False, server_default="URL"),
        sa.Column("source_url", sa.String(length=2048), nullable=True),
        sa.Column("display_title", sa.String(length=255), nullable=True),
        sa.Column("source_hostname", sa.String(length=255), nullable=True),
        sa.Column("state", sa.String(length=16), nullable=False, server_default="CAPTURED"),
        sa.Column("manifest_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("staging_key", sa.String(length=1024), nullable=True),
        sa.Column("target_collection_id", sa.Integer(), nullable=True),
        sa.Column("requested_tags_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("background_job_id", sa.String(length=64), nullable=True),
        sa.Column("resulting_model_id", sa.Integer(), nullable=True),
        sa.Column("error_code", sa.String(length=128), nullable=True),
        sa.Column("retryable", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["target_collection_id"], ["collections.id"]),
        sa.ForeignKeyConstraint(["background_job_id"], ["background_jobs.id"]),
        sa.ForeignKeyConstraint(["resulting_model_id"], ["models.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in (
        "owner_user_id",
        "source_kind",
        "state",
        "target_collection_id",
        "background_job_id",
        "resulting_model_id",
        "retryable",
        "created_at",
        "updated_at",
    ):
        op.create_index(f"ix_inbox_items_{column}", "inbox_items", [column])


def downgrade() -> None:
    op.drop_table("inbox_items")
