"""preserve inbox history when terminal jobs are pruned

Revision ID: a7c9e1b5d3f2
Revises: f9a7c3e5b1d2
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "a7c9e1b5d3f2"
down_revision: str | None = "f9a7c3e5b1d2"
branch_labels = None
depends_on = None

_FK_NAME = "fk_inbox_items_background_job_id_background_jobs"


def _reflected_inbox_table(bind) -> sa.Table:
    """Reflect the legacy table and name its otherwise anonymous FK."""
    table = sa.Table("inbox_items", sa.MetaData(), autoload_with=bind)
    for constraint in table.foreign_key_constraints:
        if constraint.column_keys == ["background_job_id"]:
            constraint.name = _FK_NAME
            break
    else:
        raise RuntimeError("inbox_items background job foreign key is missing")
    return table


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        # The original pending-import migration created this FK unnamed.
        # Reflecting and naming it explicitly lets batch mode replace it
        # portably while preserving all existing columns, indexes, and rows.
        table = _reflected_inbox_table(bind)
        with op.batch_alter_table(
            "inbox_items",
            copy_from=table,
        ) as batch:
            batch.drop_constraint(_FK_NAME, type_="foreignkey")
            batch.create_foreign_key(
                _FK_NAME,
                "background_jobs",
                ["background_job_id"],
                ["id"],
                ondelete="SET NULL",
            )
        return

    foreign_keys = sa.inspect(bind).get_foreign_keys("inbox_items")
    existing = next(
        (
            fk
            for fk in foreign_keys
            if fk.get("constrained_columns") == ["background_job_id"]
            and fk.get("referred_table") == "background_jobs"
        ),
        None,
    )
    if existing is None:
        raise RuntimeError("inbox_items background job foreign key is missing")
    op.drop_constraint(
        str(existing.get("name") or _FK_NAME), "inbox_items", type_="foreignkey"
    )
    op.create_foreign_key(
        _FK_NAME,
        "inbox_items",
        "background_jobs",
        ["background_job_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        table = _reflected_inbox_table(bind)
        with op.batch_alter_table(
            "inbox_items",
            copy_from=table,
        ) as batch:
            batch.drop_constraint(_FK_NAME, type_="foreignkey")
            batch.create_foreign_key(
                _FK_NAME,
                "background_jobs",
                ["background_job_id"],
                ["id"],
            )
        return

    op.drop_constraint(_FK_NAME, "inbox_items", type_="foreignkey")
    op.create_foreign_key(
        _FK_NAME,
        "inbox_items",
        "background_jobs",
        ["background_job_id"],
        ["id"],
    )
