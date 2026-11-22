"""merge capture import intent with main

Revision ID: 58f2cf2d8d56
Revises: 6af95a6c6f24, efe11e532cb1
Create Date: 2026-11-22 02:41:06.359066

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "58f2cf2d8d56"
down_revision: Union[str, Sequence[str], None] = ("6af95a6c6f24", "efe11e532cb1")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
