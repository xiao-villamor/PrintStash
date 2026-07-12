"""add OctoPrint provider credentials

Revision ID: e2b6c9a4f7d3
Revises: d1a4e7c2f6b9
Create Date: 2026-07-12 15:00:00
"""

from alembic import op
import sqlalchemy as sa


revision = "e2b6c9a4f7d3"
down_revision = "d1a4e7c2f6b9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "printers", sa.Column("octoprint_url", sa.String(length=512), nullable=True)
    )
    op.add_column(
        "printers",
        sa.Column("octoprint_api_key", sa.String(length=255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("printers", "octoprint_api_key")
    op.drop_column("printers", "octoprint_url")
