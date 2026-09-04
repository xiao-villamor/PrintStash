"""provider connections and browser pairing

Revision ID: fb14d5e8a7c3
Revises: fa13c4e7b9d2
"""

import sqlalchemy as sa

from alembic import op

revision = "fb14d5e8a7c3"
down_revision = "fa13c4e7b9d2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "provider_connections",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("access_token", sa.Text(), nullable=True),
        sa.Column("refresh_token", sa.Text(), nullable=True),
        sa.Column("credential_secret", sa.Text(), nullable=True),
        sa.Column("token_expires_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id", "provider", name="uq_provider_connection_user_provider"
        ),
    )
    op.create_index(
        "ix_provider_connections_user_id", "provider_connections", ["user_id"]
    )
    op.create_index(
        "ix_provider_connections_provider", "provider_connections", ["provider"]
    )
    op.create_table(
        "provider_oauth_states",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("state_hash", sa.String(64), nullable=False),
        sa.Column("redirect_uri", sa.String(2048), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("used_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("state_hash"),
    )
    for col in ("user_id", "state_hash", "expires_at", "used_at"):
        op.create_index(
            f"ix_provider_oauth_states_{col}", "provider_oauth_states", [col]
        )
    op.create_table(
        "browser_pairing_codes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("code_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("used_at", sa.DateTime(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code_hash"),
    )
    for col in ("user_id", "code_hash", "expires_at", "used_at"):
        op.create_index(
            f"ix_browser_pairing_codes_{col}", "browser_pairing_codes", [col]
        )
    op.create_table(
        "browser_devices",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("credential_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("last_used_at", sa.DateTime(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "name", name="uq_browser_device_user_name"),
        sa.UniqueConstraint("credential_hash"),
    )
    for col in ("user_id", "credential_hash", "revoked_at"):
        op.create_index(f"ix_browser_devices_{col}", "browser_devices", [col])


def downgrade() -> None:
    op.drop_table("browser_devices")
    op.drop_table("browser_pairing_codes")
    op.drop_table("provider_oauth_states")
    op.drop_table("provider_connections")
