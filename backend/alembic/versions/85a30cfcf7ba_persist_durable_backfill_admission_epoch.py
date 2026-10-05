"""Reserve a backfill slot durably before handing its attempt to the engine.

Existing execution/submission epochs and attempts remain unchanged. The new
nullable marker grants authority only when it matches execution_epoch. Jobs
are rebuilt from pinned pre/post-revision shapes, including their original
ownership and active-subject index, so offline SQLite review is available.

Revision ID: 85a30cfcf7ba
Revises: 2c02cfac74a0
Create Date: 2026-10-05 03:41:47.743777

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "85a30cfcf7ba"
down_revision: Union[str, Sequence[str], None] = "2c02cfac74a0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _previous_job_table() -> sa.Table:
    """The literal Jobs shape at 2c02cfac74a0; no live model imports."""
    metadata = sa.MetaData()
    # FK target identity only; this migration never changes or creates users.
    sa.Table("users", metadata, sa.Column("id", sa.Integer(), primary_key=True))
    table = sa.Table(
        "jobs",
        metadata,
        sa.Column("id", sa.String(64), nullable=False),
        sa.Column("owner_user_id", sa.Integer(), nullable=True),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("status_json", sa.Text(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("subject_key", sa.String(255), nullable=False),
        sa.Column("priority", sa.Text(), nullable=False),
        sa.Column("resubmits", sa.Integer(), nullable=False),
        sa.Column("app_version", sa.String(64), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("execution_epoch", sa.String(64), nullable=False),
        sa.Column("submitted_epoch", sa.String(64), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"]),
        sa.CheckConstraint(
            "state IN ('queued', 'running', 'interrupted', 'completed', 'failed', 'cancelled')",
            name="ck_jobs_state_values",
        ),
        sa.CheckConstraint(
            "kind IN ('administration.audit', 'backups.automatic', 'backups.create', 'backups.retry_destination', 'backups.trash_gc', 'derivatives.gcode', 'derivatives.mesh', 'derivatives.toolpath', 'derivatives.viewer_stl', 'identity.retention', 'inference.model_download', 'ingestion.archive_inspect', 'ingestion.archive_selection', 'ingestion.artifact_upload', 'ingestion.collection', 'ingestion.inbox_import', 'ingestion.inbox_resolve', 'ingestion.inbox_retention', 'ingestion.library_import', 'ingestion.upload', 'ingestion.upload_recovery', 'ingestion.url', 'ingestion.url_selection', 'notifications.deliver', 'notifications.retention', 'printing.dispatch', 'search.caption', 'search.caption_queue', 'search.expand', 'search.generation', 'search.index', 'search.project', 'search.repair', 'similarity.analyze', 'sources.scan', 'storage.inventory', 'storage.migrate', 'work.housekeeping')",
            name="ck_jobs_kind_values",
        ),
        sa.CheckConstraint(
            "priority IN ('interactive', 'backfill')", name="ck_jobs_priority_values"
        ),
        sa.CheckConstraint(
            "length(execution_epoch) > 0", name="ck_jobs_execution_epoch_present"
        ),
        sa.CheckConstraint(
            "submitted_epoch IS NULL OR length(submitted_epoch) > 0",
            name="ck_jobs_submitted_epoch_present",
        ),
    )
    sa.Index("ix_jobs_created_at", table.c.created_at)
    sa.Index("ix_jobs_finished_at", table.c.finished_at)
    sa.Index("ix_jobs_kind", table.c.kind)
    sa.Index(
        "ix_jobs_owner_state_updated",
        table.c.owner_user_id,
        table.c.state,
        table.c.updated_at,
    )
    sa.Index("ix_jobs_owner_user_id", table.c.owner_user_id)
    sa.Index("ix_jobs_state", table.c.state)
    sa.Index("ix_jobs_subject_key", table.c.subject_key)
    sa.Index("ix_jobs_updated_at", table.c.updated_at)
    sa.Index(
        "uq_jobs_active_subject",
        table.c.kind,
        table.c.subject_key,
        unique=True,
        sqlite_where=sa.text("state IN ('queued', 'running', 'interrupted')"),
        postgresql_where=sa.text("state IN ('queued', 'running', 'interrupted')"),
    )
    return table


def _current_job_table() -> sa.Table:
    """The pinned Jobs shape after this revision, used only for downgrade."""
    table = _previous_job_table()
    table.append_column(
        sa.Column("backfill_admission_epoch", sa.String(64), nullable=True)
    )
    table.append_constraint(
        sa.CheckConstraint(
            "backfill_admission_epoch IS NULL OR length(backfill_admission_epoch) > 0",
            name="ck_jobs_backfill_admission_epoch_present",
        )
    )
    return table


def upgrade() -> None:
    """Upgrade schema."""
    # ### commands auto generated by Alembic - please adjust! ###
    with op.batch_alter_table(
        "jobs", schema=None, copy_from=_previous_job_table()
    ) as batch_op:
        batch_op.add_column(
            sa.Column("backfill_admission_epoch", sa.String(length=64), nullable=True)
        )
        batch_op.create_check_constraint(
            batch_op.f("ck_jobs_backfill_admission_epoch_present"),
            "backfill_admission_epoch IS NULL OR length(backfill_admission_epoch) > 0",
        )

    # ### end Alembic commands ###


def downgrade() -> None:
    """Downgrade schema."""
    # ### commands auto generated by Alembic - please adjust! ###
    with op.batch_alter_table(
        "jobs", schema=None, copy_from=_current_job_table()
    ) as batch_op:
        batch_op.drop_constraint(
            batch_op.f("ck_jobs_backfill_admission_epoch_present"), type_="check"
        )
        batch_op.drop_column("backfill_admission_epoch")

    # ### end Alembic commands ###
