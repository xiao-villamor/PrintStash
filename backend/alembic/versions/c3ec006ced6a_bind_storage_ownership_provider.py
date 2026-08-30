"""Bind storage ownership provider and quarantine incomplete backup receipts.

Revision ID: c3ec006ced6a
Revises: 8c44c3bfef74
Create Date: 2026-08-30 11:03:43.224389

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c3ec006ced6a"
down_revision: Union[str, Sequence[str], None] = "8c44c3bfef74"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# ``copy_from`` is deliberately literal.  SQLite cannot render a batch rebuild
# in offline mode by reflecting a live table, and reflecting here would also let
# a drifted install silently change the shape of this historical migration.
_legacy_metadata = sa.MetaData()
_legacy_owned_storage_objects = sa.Table(
    "owned_storage_objects",
    _legacy_metadata,
    sa.Column("id", sa.Integer(), nullable=False),
    sa.Column("backend", sa.String(length=32), nullable=False),
    sa.Column("namespace", sa.String(length=1024), nullable=False),
    sa.Column("key", sa.String(length=2048), nullable=False),
    sa.Column("object_kind", sa.String(length=64), nullable=False),
    sa.Column("token", sa.String(length=64), nullable=True),
    sa.Column("size_bytes", sa.Integer(), nullable=True),
    sa.Column("etag", sa.String(length=255), nullable=True),
    sa.Column("device", sa.BigInteger(), nullable=True),
    sa.Column("inode", sa.BigInteger(), nullable=True),
    sa.Column("ctime_ns", sa.BigInteger(), nullable=True),
    sa.Column("created_at", sa.DateTime(), nullable=False),
    sa.Column("version_id", sa.String(length=1024), nullable=True),
    sa.Column("state", sa.String(length=16), nullable=False),
    sa.Column("sha256", sa.String(length=64), nullable=True),
    sa.Column("committed_at", sa.DateTime(), nullable=True),
    sa.Column("last_error", sa.String(length=255), nullable=True),
    sa.PrimaryKeyConstraint("id"),
    sa.UniqueConstraint("backend", "namespace", "key", name="uq_owned_storage_locator"),
)
for _name, _columns in (
    ("ix_owned_storage_objects_backend", ("backend",)),
    ("ix_owned_storage_objects_namespace", ("namespace",)),
    ("ix_owned_storage_objects_object_kind", ("object_kind",)),
    ("ix_owned_storage_objects_state", ("state",)),
    ("ix_owned_storage_objects_sha256", ("sha256",)),
    ("ix_owned_storage_objects_created_at", ("created_at",)),
    ("ix_owned_storage_objects_committed_at", ("committed_at",)),
):
    sa.Index(_name, *_columns, _table=_legacy_owned_storage_objects)

_provider_metadata = sa.MetaData()
_provider_owned_storage_objects = sa.Table(
    "owned_storage_objects",
    _provider_metadata,
    sa.Column("id", sa.Integer(), nullable=False),
    sa.Column("backend", sa.String(length=32), nullable=False),
    sa.Column("namespace", sa.String(length=1024), nullable=False),
    sa.Column("key", sa.String(length=2048), nullable=False),
    sa.Column("object_kind", sa.String(length=64), nullable=False),
    sa.Column("provider_ref", sa.String(length=64), nullable=True),
    sa.Column("token", sa.String(length=64), nullable=True),
    sa.Column("size_bytes", sa.Integer(), nullable=True),
    sa.Column("etag", sa.String(length=255), nullable=True),
    sa.Column("device", sa.BigInteger(), nullable=True),
    sa.Column("inode", sa.BigInteger(), nullable=True),
    sa.Column("ctime_ns", sa.BigInteger(), nullable=True),
    sa.Column("created_at", sa.DateTime(), nullable=False),
    sa.Column("version_id", sa.String(length=1024), nullable=True),
    sa.Column("state", sa.String(length=16), nullable=False),
    sa.Column("sha256", sa.String(length=64), nullable=True),
    sa.Column("committed_at", sa.DateTime(), nullable=True),
    sa.Column("last_error", sa.String(length=255), nullable=True),
    sa.PrimaryKeyConstraint("id"),
    sa.UniqueConstraint(
        "backend",
        "provider_ref",
        "namespace",
        "key",
        name="uq_owned_storage_provider_locator",
    ),
    sa.Index(
        "uq_owned_storage_legacy_locator",
        "backend",
        "namespace",
        "key",
        unique=True,
        sqlite_where=sa.text("provider_ref IS NULL"),
        postgresql_where=sa.text("provider_ref IS NULL"),
    ),
)
for _name, _columns in (
    ("ix_owned_storage_objects_backend", ("backend",)),
    ("ix_owned_storage_objects_namespace", ("namespace",)),
    ("ix_owned_storage_objects_object_kind", ("object_kind",)),
    ("ix_owned_storage_objects_state", ("state",)),
    ("ix_owned_storage_objects_sha256", ("sha256",)),
    ("ix_owned_storage_objects_created_at", ("created_at",)),
    ("ix_owned_storage_objects_committed_at", ("committed_at",)),
    ("ix_owned_storage_objects_provider_ref", ("provider_ref",)),
):
    sa.Index(_name, *_columns, _table=_provider_owned_storage_objects)


def _storage_delete_table(metadata: sa.MetaData, *, provider: bool) -> sa.Table:
    columns = [
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("backend", sa.String(length=32), nullable=False),
        sa.Column("namespace", sa.String(length=1024), nullable=False),
        sa.Column("key", sa.String(length=2048), nullable=False),
        sa.Column("object_kind", sa.String(length=64), nullable=False),
    ]
    if provider:
        columns.append(sa.Column("provider_ref", sa.String(length=64), nullable=True))
    columns.extend(
        [
            sa.Column("token", sa.String(length=64), nullable=False),
            sa.Column("size_bytes", sa.Integer(), nullable=False),
            sa.Column("sha256", sa.String(length=64), nullable=True),
            sa.Column("etag", sa.String(length=255), nullable=True),
            sa.Column("version_id", sa.String(length=1024), nullable=True),
            sa.Column("device", sa.BigInteger(), nullable=True),
            sa.Column("inode", sa.BigInteger(), nullable=True),
            sa.Column("ctime_ns", sa.BigInteger(), nullable=True),
            sa.Column("authorization_mode", sa.String(length=16), nullable=False),
            sa.Column("authorized_actor_id", sa.BigInteger(), nullable=True),
            sa.Column("authorized_at", sa.DateTime(), nullable=False),
            sa.Column("quarantine_key", sa.String(length=2048), nullable=True),
            sa.Column("quarantine_state", sa.String(length=16), nullable=False),
            sa.Column("resource_kind", sa.String(length=64), nullable=True),
            sa.Column("resource_id", sa.String(length=64), nullable=True),
            sa.Column("status", sa.String(length=16), nullable=False),
            sa.Column("attempts", sa.Integer(), nullable=False),
            sa.Column("next_attempt_at", sa.DateTime(), nullable=True),
            sa.Column("last_error", sa.String(length=255), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.Column("completed_at", sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        ]
    )
    if provider:
        columns.extend(
            [
                sa.UniqueConstraint(
                    "backend",
                    "provider_ref",
                    "namespace",
                    "key",
                    "token",
                    name="uq_storage_delete_intent_provider_receipt",
                ),
                sa.Index(
                    "uq_storage_delete_intent_legacy_receipt",
                    "backend",
                    "namespace",
                    "key",
                    "token",
                    unique=True,
                    sqlite_where=sa.text("provider_ref IS NULL"),
                    postgresql_where=sa.text("provider_ref IS NULL"),
                ),
            ]
        )
    else:
        columns.append(
            sa.UniqueConstraint(
                "backend",
                "namespace",
                "key",
                "token",
                name="uq_storage_delete_intent_receipt",
            )
        )
    table = sa.Table("storage_delete_intents", metadata, *columns)
    for name, column in (
        ("authorization_mode", "authorization_mode"),
        ("authorized_at", "authorized_at"),
        ("backend", "backend"),
        ("object_kind", "object_kind"),
        ("resource_kind", "resource_kind"),
        ("resource_id", "resource_id"),
        ("sha256", "sha256"),
        ("status", "status"),
        ("next_attempt_at", "next_attempt_at"),
        ("created_at", "created_at"),
    ):
        sa.Index(f"ix_storage_delete_intents_{name}", table.c[column])
    if provider:
        sa.Index("ix_storage_delete_intents_provider_ref", table.c.provider_ref)
    return table


_legacy_storage_delete_intents = _storage_delete_table(sa.MetaData(), provider=False)
_provider_storage_delete_intents = _storage_delete_table(sa.MetaData(), provider=True)


def upgrade() -> None:
    """Add provider identity without silently rebinding historical receipts."""
    # ### commands auto generated by Alembic - please adjust! ###
    with op.batch_alter_table(
        "owned_storage_objects",
        schema=None,
        copy_from=_legacy_owned_storage_objects,
    ) as batch_op:
        batch_op.add_column(
            sa.Column("provider_ref", sa.String(length=64), nullable=True)
        )
        batch_op.drop_constraint(batch_op.f("uq_owned_storage_locator"), type_="unique")
        batch_op.create_index(
            batch_op.f("ix_owned_storage_objects_provider_ref"),
            ["provider_ref"],
            unique=False,
        )
        batch_op.create_index(
            "uq_owned_storage_legacy_locator",
            ["backend", "namespace", "key"],
            unique=True,
            sqlite_where=sa.text("provider_ref IS NULL"),
            postgresql_where=sa.text("provider_ref IS NULL"),
        )
        batch_op.create_unique_constraint(
            "uq_owned_storage_provider_locator",
            ["backend", "provider_ref", "namespace", "key"],
        )

    with op.batch_alter_table(
        "storage_delete_intents",
        schema=None,
        copy_from=_legacy_storage_delete_intents,
    ) as batch_op:
        batch_op.add_column(
            sa.Column("provider_ref", sa.String(length=64), nullable=True)
        )
        batch_op.drop_constraint(
            batch_op.f("uq_storage_delete_intent_receipt"), type_="unique"
        )
        batch_op.create_index(
            batch_op.f("ix_storage_delete_intents_provider_ref"),
            ["provider_ref"],
            unique=False,
        )
        batch_op.create_index(
            "uq_storage_delete_intent_legacy_receipt",
            ["backend", "namespace", "key", "token"],
            unique=True,
            sqlite_where=sa.text("provider_ref IS NULL"),
            postgresql_where=sa.text("provider_ref IS NULL"),
        )
        batch_op.create_unique_constraint(
            "uq_storage_delete_intent_provider_receipt",
            ["backend", "provider_ref", "namespace", "key", "token"],
        )

    # Rows written by the pre-provider backup publisher have no immutable
    # destination identity.  Keep all receipt/evidence columns intact, but do
    # not let recovery treat those rows as proof for the current target.  Every
    # pre-provider backup receipt is quarantined, including PENDING rows: those
    # rows lack the provider binding needed to decide which target may be
    # probed after a configuration change.  New PENDING rows carry a provider
    # ref and are handled by the backup-specific reconciler.
    op.execute(
        sa.text(
            "UPDATE owned_storage_objects "
            "SET state = 'blocked', committed_at = NULL, "
            "last_error = 'backup_s3_adoption_required' "
            "WHERE backend = 'backup-s3' "
            "AND object_kind IN ('backup', 'backup-legacy') "
            "AND provider_ref IS NULL"
        )
    )

    # ### end Alembic commands ###


def downgrade() -> None:
    """Restore the legacy locator constraint only when it remains lossless."""
    # SQL rendering cannot inspect rows.  The online guard is what prevents a
    # downgrade from silently collapsing two provider-bound receipts into the
    # old locator identity; offline SQL remains renderable for review.
    # Restore migration-quarantined legacy rows in both online and offline
    # renderings. Offline SQL is used for reviewed operator downgrade plans,
    # so omitting this update there would leave rows blocked after downgrade.
    op.execute(
        sa.text(
            "UPDATE owned_storage_objects SET state = 'committed', "
            "committed_at = created_at, last_error = NULL "
            "WHERE backend = 'backup-s3' "
            "AND object_kind IN ('backup', 'backup-legacy') "
            "AND provider_ref IS NULL AND state = 'blocked' "
            "AND last_error = 'backup_s3_adoption_required'"
        )
    )
    if not op.get_context().as_sql:
        bind = op.get_bind()
        conflicts = bind.execute(
            sa.text(
                "SELECT backend, namespace, key "
                "FROM owned_storage_objects "
                "GROUP BY backend, namespace, key "
                "HAVING COUNT(*) > 1"
            )
        ).fetchone()
        if conflicts is not None:
            raise RuntimeError(
                "cannot downgrade storage ownership: provider-distinct rows "
                "would collide under uq_owned_storage_locator"
            )
        intent_conflicts = bind.execute(
            sa.text(
                "SELECT backend, namespace, key, token "
                "FROM storage_delete_intents "
                "GROUP BY backend, namespace, key, token "
                "HAVING COUNT(*) > 1"
            )
        ).fetchone()
        if intent_conflicts is not None:
            raise RuntimeError(
                "cannot downgrade storage deletion intents: provider-distinct "
                "rows would collide under uq_storage_delete_intent_receipt"
            )
    # ### commands auto generated by Alembic - please adjust! ###
    with op.batch_alter_table(
        "owned_storage_objects",
        schema=None,
        copy_from=_provider_owned_storage_objects,
    ) as batch_op:
        batch_op.drop_constraint("uq_owned_storage_provider_locator", type_="unique")
        batch_op.drop_index("uq_owned_storage_legacy_locator")
        batch_op.drop_index(batch_op.f("ix_owned_storage_objects_provider_ref"))
        batch_op.create_unique_constraint(
            batch_op.f("uq_owned_storage_locator"), ["backend", "namespace", "key"]
        )
        batch_op.drop_column("provider_ref")

    with op.batch_alter_table(
        "storage_delete_intents",
        schema=None,
        copy_from=_provider_storage_delete_intents,
    ) as batch_op:
        batch_op.drop_constraint(
            "uq_storage_delete_intent_provider_receipt", type_="unique"
        )
        batch_op.drop_index("uq_storage_delete_intent_legacy_receipt")
        batch_op.drop_index(batch_op.f("ix_storage_delete_intents_provider_ref"))
        batch_op.create_unique_constraint(
            batch_op.f("uq_storage_delete_intent_receipt"),
            ["backend", "namespace", "key", "token"],
        )
        batch_op.drop_column("provider_ref")

    # ### end Alembic commands ###
