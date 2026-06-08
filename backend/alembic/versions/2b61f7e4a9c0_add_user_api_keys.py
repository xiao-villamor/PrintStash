"""add user api keys

Revision ID: 2b61f7e4a9c0
Revises: d4a7c2e9b6f1
Create Date: 2026-06-08 10:00:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "2b61f7e4a9c0"
down_revision = "d4a7c2e9b6f1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "api_keys",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("key_hash", sa.String(length=64), nullable=False),
        sa.Column("prefix", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("api_keys", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_api_keys_user_id"), ["user_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_api_keys_key_hash"), ["key_hash"], unique=True)
        batch_op.create_index(batch_op.f("ix_api_keys_prefix"), ["prefix"], unique=False)
        batch_op.create_index(batch_op.f("ix_api_keys_revoked_at"), ["revoked_at"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("api_keys", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_api_keys_revoked_at"))
        batch_op.drop_index(batch_op.f("ix_api_keys_prefix"))
        batch_op.drop_index(batch_op.f("ix_api_keys_key_hash"))
        batch_op.drop_index(batch_op.f("ix_api_keys_user_id"))
    op.drop_table("api_keys")
