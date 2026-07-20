"""add per-printer permissions

Revision ID: a2f7c9d4e6b1
Revises: e9c3a6b2d5f8
Create Date: 2026-07-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a2f7c9d4e6b1"
down_revision: str | None = "e9c3a6b2d5f8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "printer_permissions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("printer_id", sa.Integer(), nullable=False),
        sa.Column(
            "role",
            sa.Enum("VIEW", "PRINT", "CONTROL", "ADMIN", name="printerrole"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["printer_id"], ["printers.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id",
            "printer_id",
            name="uq_printer_permissions_user_printer",
        ),
    )
    op.create_index(
        op.f("ix_printer_permissions_printer_id"),
        "printer_permissions",
        ["printer_id"],
    )
    op.create_index(
        op.f("ix_printer_permissions_role"), "printer_permissions", ["role"]
    )
    op.create_index(
        op.f("ix_printer_permissions_user_id"),
        "printer_permissions",
        ["user_id"],
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_printer_permissions_user_id"), table_name="printer_permissions"
    )
    op.drop_index(op.f("ix_printer_permissions_role"), table_name="printer_permissions")
    op.drop_index(
        op.f("ix_printer_permissions_printer_id"), table_name="printer_permissions"
    )
    op.drop_table("printer_permissions")
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        sa.Enum(name="printerrole").drop(bind, checkfirst=True)
