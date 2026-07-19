"""add persistent vault audit runs and findings

Revision ID: c7a1e4d9b2f6
Revises: b5d9f3a7c2e4
Create Date: 2026-07-19
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c7a1e4d9b2f6"
down_revision: str | None = "b5d9f3a7c2e4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "vault_audit_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("requested_by", sa.Integer(), nullable=False),
        sa.Column("mode", sa.String(length=16), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False, server_default="PENDING"),
        sa.Column("info_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("warning_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("critical_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("progress", sa.Float(), nullable=False, server_default="0"),
        sa.Column("current_phase", sa.String(length=64), nullable=True),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("error_code", sa.String(length=128), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["requested_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in ("requested_by", "mode", "state", "created_at"):
        op.create_index(f"ix_vault_audit_runs_{column}", "vault_audit_runs", [column])

    op.create_table(
        "vault_audit_findings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("resource_type", sa.String(length=64), nullable=False),
        sa.Column("resource_identifier", sa.String(length=255), nullable=False),
        sa.Column("repair_action", sa.String(length=64), nullable=True),
        sa.Column("state", sa.String(length=16), nullable=False, server_default="OPEN"),
        sa.Column("details_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
        sa.Column("resolved_by", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["run_id"], ["vault_audit_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["resolved_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in ("run_id", "code", "severity", "resource_type", "state", "created_at"):
        op.create_index(
            f"ix_vault_audit_findings_{column}", "vault_audit_findings", [column]
        )


def downgrade() -> None:
    op.drop_table("vault_audit_findings")
    op.drop_table("vault_audit_runs")
