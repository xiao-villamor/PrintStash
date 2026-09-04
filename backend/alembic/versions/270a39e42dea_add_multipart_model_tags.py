"""Give multipart compositions their own direct, non-inherited tags.

The association is separate from Model tags: grouping Models never rewrites
the taxonomy attached to any member.

Revision ID: 270a39e42dea
Revises: 3beaa172254a
Create Date: 2026-09-02 13:48:30.404393

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "270a39e42dea"
down_revision: Union[str, Sequence[str], None] = "3beaa172254a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create the direct-tag association for multipart compositions."""
    op.create_table(
        "multipart_model_tags",
        sa.Column("multipart_model_id", sa.Integer(), nullable=False),
        sa.Column("tag_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["multipart_model_id"],
            ["multipart_models.id"],
            name=op.f("fk_multipart_model_tags_multipart_model_id_multipart_models"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tag_id"],
            ["tags.id"],
            name=op.f("fk_multipart_model_tags_tag_id_tags"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "multipart_model_id", "tag_id", name=op.f("pk_multipart_model_tags")
        ),
    )
    with op.batch_alter_table("multipart_model_tags", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_multipart_model_tags_tag_id"), ["tag_id"], unique=False
        )


def downgrade() -> None:
    """Remove multipart tags without changing Model tags."""
    with op.batch_alter_table("multipart_model_tags", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_multipart_model_tags_tag_id"))

    op.drop_table("multipart_model_tags")
