"""Link Model-owned Documents while retaining existing library Documents.

Revision ID: a15ceb9423a1
Revises: f766f983403b
Create Date: 2026-11-23 13:29:48.469052

"""

from contextlib import contextmanager
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a15ceb9423a1"
down_revision: Union[str, Sequence[str], None] = "f766f983403b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


@contextmanager
def _preserve_sqlite_triggers():
    """SQLite validates cross-table triggers while renaming a rebuilt table.

    Save and reinstall browse, authorization and edit revision triggers.
    PostgreSQL alters the table in place and retains its triggers itself.
    """
    bind = op.get_bind()
    triggers = []
    if bind.dialect.name == "sqlite":
        triggers = bind.execute(
            sa.text(
                "SELECT name, sql FROM sqlite_master WHERE type = 'trigger' AND (tbl_name = 'documents' OR sql LIKE '%documents%')"
            )
        ).all()
        for name, _sql in triggers:
            quoted = name.replace('"', '""')
            bind.exec_driver_sql(f'DROP TRIGGER "{quoted}"')
    try:
        yield
    finally:
        for _name, sql in triggers:
            bind.exec_driver_sql(sql)


def upgrade() -> None:
    """Upgrade schema."""
    with (
        _preserve_sqlite_triggers(),
        op.batch_alter_table("documents", schema=None) as batch_op,
    ):
        batch_op.add_column(sa.Column("model_id", sa.Integer(), nullable=True))
        batch_op.create_check_constraint(
            batch_op.f("ck_documents_document_model_owns_scope"),
            "model_id IS NULL OR (collection_id IS NULL AND multipart_model_id IS NULL)",
        )
        batch_op.create_index(
            batch_op.f("ix_documents_model_id"), ["model_id"], unique=False
        )
        batch_op.create_foreign_key(
            batch_op.f("fk_documents_model_id_models"), "models", ["model_id"], ["id"]
        )


def downgrade() -> None:
    """Return Model-owned Documents to their Model's Collection."""
    op.execute(
        """
        UPDATE documents
        SET collection_id = (SELECT collection_id FROM models WHERE id = documents.model_id),
            deleted_at = COALESCE(deleted_at, (SELECT deleted_at FROM models WHERE id = documents.model_id)),
            model_id = NULL
        WHERE model_id IS NOT NULL
        """
    )
    with (
        _preserve_sqlite_triggers(),
        op.batch_alter_table("documents", schema=None) as batch_op,
    ):
        batch_op.drop_constraint(
            batch_op.f("fk_documents_model_id_models"), type_="foreignkey"
        )
        batch_op.drop_index(batch_op.f("ix_documents_model_id"))
        batch_op.drop_constraint(
            batch_op.f("ck_documents_document_model_owns_scope"), type_="check"
        )
        batch_op.drop_column("model_id")
