"""Join the Library browse and Model preview migration branches.

Revision ID: e2115db53e8c
Revises: 16c087bb845e, 55535d2c13e3
Create Date: 2026-11-16 09:16:58.682329

"""

from typing import Sequence, Union

# revision identifiers, used by Alembic.
revision: str = "e2115db53e8c"
down_revision: Union[str, Sequence[str], None] = ("16c087bb845e", "55535d2c13e3")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
