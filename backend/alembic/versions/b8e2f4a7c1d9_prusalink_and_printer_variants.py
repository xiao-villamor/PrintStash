"""add PrusaLink credentials and printer integration variants

Revision ID: b8e2f4a7c1d9
Revises: 9a1e5c7d3f02
Create Date: 2026-07-11 12:00:00
"""

from alembic import op
import sqlalchemy as sa


revision = "b8e2f4a7c1d9"
down_revision = "9a1e5c7d3f02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "printers", sa.Column("provider_variant", sa.String(length=64), nullable=True)
    )
    op.add_column(
        "printers", sa.Column("prusalink_url", sa.String(length=512), nullable=True)
    )
    op.add_column(
        "printers",
        sa.Column("prusalink_auth_mode", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "printers",
        sa.Column("prusalink_username", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "printers",
        sa.Column("prusalink_password", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "printers", sa.Column("prusalink_api_key", sa.String(length=255), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("printers", "prusalink_api_key")
    op.drop_column("printers", "prusalink_password")
    op.drop_column("printers", "prusalink_username")
    op.drop_column("printers", "prusalink_auth_mode")
    op.drop_column("printers", "prusalink_url")
    op.drop_column("printers", "provider_variant")
