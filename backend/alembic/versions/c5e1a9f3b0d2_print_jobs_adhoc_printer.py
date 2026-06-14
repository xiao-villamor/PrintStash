"""allow print jobs without a registered printer

Makes ``print_jobs.printer_id`` nullable and adds a free-text
``printer_name`` so history can be logged against ad-hoc printers.

Revision ID: c5e1a9f3b0d2
Revises: b2c8d4e6f1a0
Create Date: 2026-06-14 12:00:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "c5e1a9f3b0d2"
down_revision = "b2c8d4e6f1a0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("print_jobs", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("printer_name", sa.String(length=128), nullable=True)
        )
        batch_op.alter_column(
            "printer_id", existing_type=sa.Integer(), nullable=True
        )


def downgrade() -> None:
    with op.batch_alter_table("print_jobs", schema=None) as batch_op:
        batch_op.alter_column(
            "printer_id", existing_type=sa.Integer(), nullable=False
        )
        batch_op.drop_column("printer_name")
