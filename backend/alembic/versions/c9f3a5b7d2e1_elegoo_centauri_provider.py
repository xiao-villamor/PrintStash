"""add Elegoo Centauri Carbon provider credentials

Revision ID: c9f3a5b7d2e1
Revises: b8e2f4a7c1d9
Create Date: 2026-07-12 12:00:00
"""

from alembic import op
import sqlalchemy as sa


revision = "c9f3a5b7d2e1"
down_revision = "b8e2f4a7c1d9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "printers",
        sa.Column("elegoo_centauri_host", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "printers",
        sa.Column("elegoo_centauri_access_code", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "printers",
        sa.Column("elegoo_centauri_mainboard_id", sa.String(length=128), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("printers", "elegoo_centauri_mainboard_id")
    op.drop_column("printers", "elegoo_centauri_access_code")
    op.drop_column("printers", "elegoo_centauri_host")
