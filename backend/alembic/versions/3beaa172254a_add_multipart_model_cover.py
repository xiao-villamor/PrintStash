"""Persist the Model a Multipart Model uses as its representative cover.

Revision ID: 3beaa172254a
Revises: 7f460c4cf1f3
Create Date: 2026-09-02 11:20:53.805466

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "3beaa172254a"
down_revision: Union[str, Sequence[str], None] = "7f460c4cf1f3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _multipart_models_table(*, with_cover: bool) -> sa.Table:
    """Pinned table shape used by SQLite batch rebuilds in this revision."""
    metadata = sa.MetaData()
    sa.Table("collections", metadata, sa.Column("id", sa.Integer(), primary_key=True))
    sa.Table("models", metadata, sa.Column("id", sa.Integer(), primary_key=True))
    sa.Table("users", metadata, sa.Column("id", sa.Integer(), primary_key=True))
    columns: list[sa.Column] = [
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("slug", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("collection_id", sa.Integer(), nullable=True),
    ]
    if with_cover:
        columns.append(sa.Column("cover_model_id", sa.Integer(), nullable=True))
    columns.extend(
        [
            sa.Column("created_by", sa.Integer(), nullable=True),
            sa.Column("updated_by", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        ]
    )
    constraints: list[sa.Constraint] = [
        sa.ForeignKeyConstraint(
            ["collection_id"],
            ["collections.id"],
            name="fk_multipart_models_collection_id_collections",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name="fk_multipart_models_created_by_users"
        ),
        sa.ForeignKeyConstraint(
            ["updated_by"], ["users.id"], name="fk_multipart_models_updated_by_users"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_multipart_models"),
        sa.UniqueConstraint("slug", name="uq_multipart_models_slug"),
    ]
    if with_cover:
        constraints.append(
            sa.ForeignKeyConstraint(
                ["cover_model_id"],
                ["models.id"],
                name="fk_multipart_models_cover_model_id_models",
                ondelete="SET NULL",
            )
        )
    table = sa.Table("multipart_models", metadata, *columns, *constraints)
    sa.Index("ix_multipart_models_collection_id", table.c.collection_id)
    sa.Index("ix_multipart_models_name", table.c.name)
    sa.Index("ix_multipart_models_slug", table.c.slug)
    sa.Index("ix_multipart_models_updated_at", table.c.updated_at)
    if with_cover:
        sa.Index("ix_multipart_models_cover_model_id", table.c.cover_model_id)
    return table


def upgrade() -> None:
    """Add the nullable, indexed cover Model reference."""
    with op.batch_alter_table(
        "multipart_models",
        schema=None,
        copy_from=_multipart_models_table(with_cover=False),
    ) as batch_op:
        batch_op.add_column(sa.Column("cover_model_id", sa.Integer(), nullable=True))
        batch_op.create_index(
            batch_op.f("ix_multipart_models_cover_model_id"),
            ["cover_model_id"],
            unique=False,
        )
        batch_op.create_foreign_key(
            batch_op.f("fk_multipart_models_cover_model_id_models"),
            "models",
            ["cover_model_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    """Remove the cover reference while preserving Multipart Models."""
    with op.batch_alter_table(
        "multipart_models",
        schema=None,
        copy_from=_multipart_models_table(with_cover=True),
    ) as batch_op:
        batch_op.drop_constraint(
            batch_op.f("fk_multipart_models_cover_model_id_models"),
            type_="foreignkey",
        )
        batch_op.drop_index(batch_op.f("ix_multipart_models_cover_model_id"))
        batch_op.drop_column("cover_model_id")
