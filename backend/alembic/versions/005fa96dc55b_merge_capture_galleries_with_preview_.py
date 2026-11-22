"""merge capture galleries with preview settings

Revision ID: 005fa96dc55b
Revises: 14982d207a3e, 3c9a19d6deb9
Create Date: 2026-11-17 00:17:13.382773

"""

from typing import Sequence, Union

# revision identifiers, used by Alembic.
revision: str = "005fa96dc55b"
down_revision: Union[str, Sequence[str], None] = ("14982d207a3e", "3c9a19d6deb9")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
