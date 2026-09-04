"""merge storage provider and schema convergence heads

Revision ID: 8b9aaacfed6b
Revises: 6acea2a5e555, c4e8a2f6b1d9
Create Date: 2026-08-28 15:26:27.025331

"""
from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "8b9aaacfed6b"
down_revision: str | Sequence[str] | None = ("6acea2a5e555", "c4e8a2f6b1d9")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
