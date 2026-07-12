"""add printer model_name and detected_model fields

Revision ID: d1a4e7c2f6b9
Revises: c9f3a5b7d2e1
Create Date: 2026-07-12 14:00:00
"""

from alembic import op
import sqlalchemy as sa


revision = "d1a4e7c2f6b9"
down_revision = "c9f3a5b7d2e1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "printers", sa.Column("model_name", sa.String(length=128), nullable=True)
    )
    op.add_column(
        "printers", sa.Column("detected_model", sa.String(length=128), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("printers", "detected_model")
    op.drop_column("printers", "model_name")
