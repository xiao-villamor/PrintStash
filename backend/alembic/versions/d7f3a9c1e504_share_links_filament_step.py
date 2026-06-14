"""share links, measured filament/duration, auto-known-good, STEP file type

Adds:
- ``share_links`` table (public expiring read-only model shares).
- ``print_jobs`` measured-outcome columns (filament + duration).
- ``system_config.auto_mark_known_good`` toggle.
- ``STEP`` to the ``filetype`` enum.

Revision ID: d7f3a9c1e504
Revises: c5e1a9f3b0d2
Create Date: 2026-06-14 13:00:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "d7f3a9c1e504"
down_revision = "c5e1a9f3b0d2"
branch_labels = None
depends_on = None


def _add_step_filetype() -> None:
    # Postgres uses a native enum type that must gain the STEP label. SQLite
    # stores the enum as a plain VARCHAR (no CHECK constraint), so it already
    # accepts the new value — nothing to do there.
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("ALTER TYPE filetype ADD VALUE IF NOT EXISTS 'STEP'")


def upgrade() -> None:
    _add_step_filetype()

    with op.batch_alter_table("print_jobs", schema=None) as batch_op:
        batch_op.add_column(sa.Column("filament_used_mm", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("filament_used_g", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("actual_duration_s", sa.Integer(), nullable=True))

    with op.batch_alter_table("system_config", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "auto_mark_known_good",
                sa.Boolean(),
                nullable=False,
                server_default=sa.true(),
            )
        )

    op.create_table(
        "share_links",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("model_id", sa.Integer(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.Column(
            "allow_download",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column(
            "access_count", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["model_id"], ["models.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("share_links", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_share_links_model_id"), ["model_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_share_links_token_hash"), ["token_hash"], unique=True
        )
        batch_op.create_index(
            batch_op.f("ix_share_links_expires_at"), ["expires_at"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_share_links_revoked_at"), ["revoked_at"], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table("share_links", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_share_links_revoked_at"))
        batch_op.drop_index(batch_op.f("ix_share_links_expires_at"))
        batch_op.drop_index(batch_op.f("ix_share_links_token_hash"))
        batch_op.drop_index(batch_op.f("ix_share_links_model_id"))
    op.drop_table("share_links")

    with op.batch_alter_table("system_config", schema=None) as batch_op:
        batch_op.drop_column("auto_mark_known_good")

    with op.batch_alter_table("print_jobs", schema=None) as batch_op:
        batch_op.drop_column("actual_duration_s")
        batch_op.drop_column("filament_used_g")
        batch_op.drop_column("filament_used_mm")

    # The STEP enum value is left in place: SQLite has no constraint to revert,
    # and Postgres enum labels cannot be dropped. Harmless either way.
