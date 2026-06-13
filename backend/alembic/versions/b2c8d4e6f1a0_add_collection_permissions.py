"""add collection permissions

Revision ID: b2c8d4e6f1a0
Revises: 6f4a2c9d8e10
Create Date: 2026-06-12 16:00:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "b2c8d4e6f1a0"
down_revision = "6f4a2c9d8e10"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "collection_permissions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("collection_id", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=5), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["collection_id"], ["collections.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "collection_id", name="uq_collection_permissions_user_collection"),
    )
    with op.batch_alter_table("collection_permissions", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_collection_permissions_collection_id"), ["collection_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_collection_permissions_role"), ["role"], unique=False)
        batch_op.create_index(batch_op.f("ix_collection_permissions_user_id"), ["user_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("collection_permissions", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_collection_permissions_user_id"))
        batch_op.drop_index(batch_op.f("ix_collection_permissions_role"))
        batch_op.drop_index(batch_op.f("ix_collection_permissions_collection_id"))
    op.drop_table("collection_permissions")
