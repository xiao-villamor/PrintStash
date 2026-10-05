"""Keep fair discovery turns and indexed recent keysets across worker restarts.

Existing library rows and scan positions are retained. New cursors start with
backfill as their next turn; the recent keyset starts empty. The cursor rebuild
uses its pinned pre-revision shape rather than live application metadata.

Revision ID: 2c02cfac74a0
Revises: 3bcdfeec6c4f
Create Date: 2026-10-05 02:53:24.278321

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "2c02cfac74a0"
down_revision: Union[str, Sequence[str], None] = "3bcdfeec6c4f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _previous_cursor_table() -> sa.Table:
    """The literal cursor shape at 3bcdfeec6c4f, pinned for batch rebuilds."""
    return sa.Table(
        "reconcile_cursors",
        sa.MetaData(),
        sa.Column("source", sa.TEXT(), nullable=False),
        sa.Column("pass_priority", sa.TEXT(), nullable=True),
        sa.Column("idle_since", sa.DATETIME(), nullable=True),
        sa.Column("idle_until", sa.DATETIME(), nullable=True),
        sa.Column(
            "scan_high_water",
            sa.INTEGER(),
            server_default=sa.text("'0'"),
            nullable=False,
        ),
        sa.Column(
            "scan_position", sa.INTEGER(), server_default=sa.text("'0'"), nullable=False
        ),
        sa.Column("nudged_at", sa.DATETIME(), nullable=True),
        sa.Column("pass_queued_at", sa.DATETIME(), nullable=True),
        sa.Column("holder", sa.VARCHAR(length=128), nullable=True),
        sa.Column("holder_expires_at", sa.DATETIME(), nullable=True),
        sa.Column("last_pass_started_at", sa.DATETIME(), nullable=True),
        sa.Column("last_pass_finished_at", sa.DATETIME(), nullable=True),
        sa.Column("last_pass_submitted", sa.INTEGER(), nullable=False),
        sa.Column("last_pass_deferred", sa.INTEGER(), nullable=False),
        sa.Column("last_occurrence_at", sa.DATETIME(), nullable=True),
        sa.PrimaryKeyConstraint("source", name="pk_reconcile_cursors"),
        sa.CheckConstraint(
            "(holder IS NULL) = (holder_expires_at IS NULL)",
            name="ck_reconcile_cursors_holder_with_expiry",
        ),
        sa.CheckConstraint(
            "(idle_since IS NULL) = (idle_until IS NULL)",
            name="ck_reconcile_cursors_idle_window_complete",
        ),
        sa.CheckConstraint(
            "pass_priority IN ('interactive', 'backfill')",
            name="ck_reconcile_cursors_pass_priority_values",
        ),
        sa.CheckConstraint(
            "(pass_queued_at IS NULL) = (pass_priority IS NULL)",
            name="ck_reconcile_cursors_pass_queued_with_priority",
        ),
        sa.CheckConstraint(
            "source IN ('administration.audit', 'backups.automatic', 'backups.create', 'backups.retry_destination', 'backups.trash_gc', 'derivatives.gcode', 'derivatives.mesh', 'derivatives.toolpath', 'derivatives.viewer_stl', 'identity.retention', 'inference.model_download', 'ingestion.archive_inspect', 'ingestion.archive_selection', 'ingestion.artifact_upload', 'ingestion.collection', 'ingestion.inbox_import', 'ingestion.inbox_resolve', 'ingestion.inbox_retention', 'ingestion.library_import', 'ingestion.upload', 'ingestion.upload_recovery', 'ingestion.url', 'ingestion.url_selection', 'notifications.deliver', 'notifications.retention', 'printing.dispatch', 'search.caption', 'search.caption_queue', 'search.expand', 'search.generation', 'search.index', 'search.project', 'search.repair', 'similarity.analyze', 'sources.scan', 'storage.inventory', 'storage.migrate', 'work.housekeeping')",
            name="ck_reconcile_cursors_source_values",
        ),
    )


def _current_cursor_table() -> sa.Table:
    """The pinned cursor shape after this revision, used only for downgrade."""
    table = _previous_cursor_table()
    table.append_column(
        sa.Column(
            "discovery_next_priority",
            sa.Text(),
            server_default="backfill",
            nullable=False,
        )
    )
    table.append_column(sa.Column("scan_recent_at", sa.DateTime(), nullable=True))
    table.append_column(sa.Column("scan_recent_file_id", sa.Integer(), nullable=True))
    table.append_constraint(
        sa.CheckConstraint(
            "discovery_next_priority IN ('interactive', 'backfill')",
            name="ck_reconcile_cursors_discovery_next_priority_values",
        )
    )
    table.append_constraint(
        sa.CheckConstraint(
            "(scan_recent_at IS NULL) = (scan_recent_file_id IS NULL)",
            name="ck_reconcile_cursors_recent_keyset_complete",
        )
    )
    table.append_constraint(
        sa.CheckConstraint(
            "scan_recent_file_id IS NULL OR scan_recent_file_id > 0",
            name="ck_reconcile_cursors_recent_keyset_positive_id",
        )
    )
    return table


def upgrade() -> None:
    """Upgrade schema."""
    # ### commands auto generated by Alembic - please adjust! ###
    with op.batch_alter_table("files", schema=None) as batch_op:
        batch_op.create_index(
            "ix_files_uploaded_id", ["uploaded_at", "id"], unique=False
        )
        batch_op.create_index(
            "ix_files_viewer_requested_id", ["viewer_requested_at", "id"], unique=False
        )

    with op.batch_alter_table(
        "reconcile_cursors", schema=None, copy_from=_previous_cursor_table()
    ) as batch_op:
        batch_op.add_column(
            sa.Column(
                "discovery_next_priority",
                sa.Text(),
                server_default="backfill",
                nullable=False,
            )
        )
        batch_op.add_column(sa.Column("scan_recent_at", sa.DateTime(), nullable=True))
        batch_op.add_column(
            sa.Column("scan_recent_file_id", sa.Integer(), nullable=True)
        )
        batch_op.create_check_constraint(
            batch_op.f("ck_reconcile_cursors_discovery_next_priority_values"),
            "discovery_next_priority IN ('interactive', 'backfill')",
        )
        batch_op.create_check_constraint(
            batch_op.f("ck_reconcile_cursors_recent_keyset_complete"),
            "(scan_recent_at IS NULL) = (scan_recent_file_id IS NULL)",
        )
        batch_op.create_check_constraint(
            batch_op.f("ck_reconcile_cursors_recent_keyset_positive_id"),
            "scan_recent_file_id IS NULL OR scan_recent_file_id > 0",
        )

    # ### end Alembic commands ###


def downgrade() -> None:
    """Downgrade schema."""
    # ### commands auto generated by Alembic - please adjust! ###
    with op.batch_alter_table(
        "reconcile_cursors", schema=None, copy_from=_current_cursor_table()
    ) as batch_op:
        batch_op.drop_constraint(
            batch_op.f("ck_reconcile_cursors_recent_keyset_positive_id"), type_="check"
        )
        batch_op.drop_constraint(
            batch_op.f("ck_reconcile_cursors_recent_keyset_complete"), type_="check"
        )
        batch_op.drop_constraint(
            batch_op.f("ck_reconcile_cursors_discovery_next_priority_values"),
            type_="check",
        )
        batch_op.drop_column("scan_recent_file_id")
        batch_op.drop_column("scan_recent_at")
        batch_op.drop_column("discovery_next_priority")

    with op.batch_alter_table("files", schema=None) as batch_op:
        batch_op.drop_index("ix_files_viewer_requested_id")
        batch_op.drop_index("ix_files_uploaded_id")

    # ### end Alembic commands ###
