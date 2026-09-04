"""merge Bambu identity and staging-index migration heads

Revision ID: f9a7c3e5b1d2
Revises: f8a6c2d9e1b4, ff18d2a4c6e8
Create Date: 2026-08-25
"""

from __future__ import annotations

revision: str = "f9a7c3e5b1d2"
down_revision: tuple[str, str] = ("f8a6c2d9e1b4", "ff18d2a4c6e8")
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Join the two independent migration branches without changing data."""


def downgrade() -> None:
    """Re-expose the two parent heads when downgrading past the merge point."""
