"""repair staging lease job index cardinality

Revision ID: ff18d2a4c6e8
Revises: fe17c8d1a0f2
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "ff18d2a4c6e8"
down_revision: str | None = "fe17c8d1a0f2"
branch_labels = None
depends_on = None

_INDEX_NAME = "ix_staging_leases_background_job_id"


def _job_index(bind) -> dict[str, object] | None:
    try:
        indexes = sa.inspect(bind).get_indexes("staging_leases")
    except sa.exc.NoInspectionAvailable:
        return None
    return next(
        (
            index
            for index in indexes
            if index.get("column_names") == ["background_job_id"]
        ),
        None,
    )


def upgrade() -> None:
    bind = op.get_bind()
    index = _job_index(bind)

    if bind.dialect.name == "sqlite":
        # Some already-stamped SQLite databases retained the legacy unique
        # index even though fe17 removed the matching table constraint. A
        # capture import owns one lease per selected file, so the job index
        # must permit several rows.
        op.execute(f"DROP INDEX IF EXISTS {_INDEX_NAME}")
        op.create_index(
            _INDEX_NAME,
            "staging_leases",
            ["background_job_id"],
            unique=False,
        )
        return

    if index is not None and bool(index.get("unique")):
        op.drop_index(str(index.get("name") or _INDEX_NAME), table_name="staging_leases")
        index = None
    if index is None:
        op.create_index(
            _INDEX_NAME,
            "staging_leases",
            ["background_job_id"],
            unique=False,
        )


def downgrade() -> None:
    # fe17's intended schema is already a non-unique job lookup index. This
    # repair migration only reconciles drift in databases stamped at fe17, so
    # downgrading must not reintroduce the invalid one-job/one-file limit.
    pass
