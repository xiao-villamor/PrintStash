"""phase 4c/4d lifecycle and audit schema

Revision ID: 69b6a6d8a1d1
Revises: 8ac4b31d82f3
Create Date: 2026-05-30 15:00:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "69b6a6d8a1d1"
down_revision = "8ac4b31d82f3"
branch_labels = None
depends_on = None


def _add_soft_delete_cols(table: str) -> None:
    is_sqlite = op.get_bind().dialect.name == "sqlite"
    op.add_column(table, sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(table, sa.Column("deleted_by", sa.Integer(), nullable=True))
    op.create_index(f"ix_{table}_deleted_at", table, ["deleted_at"], unique=False)
    if not is_sqlite:
        op.create_foreign_key(
            f"fk_{table}_deleted_by_users",
            table,
            "users",
            ["deleted_by"],
            ["id"],
        )


def upgrade() -> None:
    is_sqlite = op.get_bind().dialect.name == "sqlite"
    for table in ("files", "printers", "print_jobs", "users", "tags", "collections"):
        _add_soft_delete_cols(table)

    for table in ("models", "printers", "print_jobs", "tags", "collections"):
        op.add_column(table, sa.Column("created_by", sa.Integer(), nullable=True))
        op.add_column(table, sa.Column("updated_by", sa.Integer(), nullable=True))
        if not is_sqlite:
            op.create_foreign_key(
                f"fk_{table}_created_by_users", table, "users", ["created_by"], ["id"]
            )
            op.create_foreign_key(
                f"fk_{table}_updated_by_users", table, "users", ["updated_by"], ["id"]
            )

    op.add_column("models", sa.Column("deleted_by", sa.Integer(), nullable=True))
    if not is_sqlite:
        op.create_foreign_key(
            "fk_models_deleted_by_users", "models", "users", ["deleted_by"], ["id"]
        )

    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("actor_id", sa.Integer(), nullable=True),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("resource_type", sa.String(length=64), nullable=False),
        sa.Column("resource_id", sa.Integer(), nullable=True),
        sa.Column("diff_json", sa.String(), nullable=False, server_default="{}"),
        sa.Column("ip", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["actor_id"], ["users.id"]),
    )
    op.create_index("ix_audit_logs_actor_id", "audit_logs", ["actor_id"], unique=False)
    op.create_index("ix_audit_logs_action", "audit_logs", ["action"], unique=False)
    op.create_index("ix_audit_logs_resource_type", "audit_logs", ["resource_type"], unique=False)
    op.create_index("ix_audit_logs_resource_id", "audit_logs", ["resource_id"], unique=False)
    op.create_index("ix_audit_logs_created_at", "audit_logs", ["created_at"], unique=False)


def downgrade() -> None:
    is_sqlite = op.get_bind().dialect.name == "sqlite"
    op.drop_index("ix_audit_logs_created_at", table_name="audit_logs")
    op.drop_index("ix_audit_logs_resource_id", table_name="audit_logs")
    op.drop_index("ix_audit_logs_resource_type", table_name="audit_logs")
    op.drop_index("ix_audit_logs_action", table_name="audit_logs")
    op.drop_index("ix_audit_logs_actor_id", table_name="audit_logs")
    op.drop_table("audit_logs")

    if not is_sqlite:
        op.drop_constraint("fk_models_deleted_by_users", "models", type_="foreignkey")
    op.drop_column("models", "deleted_by")

    for table in ("models", "printers", "print_jobs", "tags", "collections"):
        if not is_sqlite:
            op.drop_constraint(f"fk_{table}_updated_by_users", table, type_="foreignkey")
            op.drop_constraint(f"fk_{table}_created_by_users", table, type_="foreignkey")
        op.drop_column(table, "updated_by")
        op.drop_column(table, "created_by")

    for table in ("files", "printers", "print_jobs", "users", "tags", "collections"):
        if not is_sqlite:
            op.drop_constraint(f"fk_{table}_deleted_by_users", table, type_="foreignkey")
        op.drop_index(f"ix_{table}_deleted_at", table_name=table)
        op.drop_column(table, "deleted_by")
        op.drop_column(table, "deleted_at")
