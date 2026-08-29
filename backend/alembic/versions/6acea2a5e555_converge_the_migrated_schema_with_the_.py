"""converge the migrated schema with the models

Revision ID: 6acea2a5e555
Revises: eb8435c9400e
Create Date: 2026-08-27 22:24:13.425624

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "6acea2a5e555"
down_revision: Union[str, Sequence[str], None] = "eb8435c9400e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# PostgreSQL stores ``sa.Enum`` as a named type. Released databases created from
# the v0.12.1 models already own these types, while chain-built databases may not.
# ``checkfirst`` covers both paths. Keep the labels aligned with SQLAlchemy's enum
# member names (upper-case); existing text columns may contain either member names
# or lower-case Python values, so the USING expression normalizes both to names.
_POSTGRES_ENUMS: dict[str, tuple[str, ...]] = {
    "captureuploadslotstate": ("PENDING", "UPLOADED"),
    "documentkind": ("MARKDOWN", "PDF", "OTHER"),
    "externallibrarywatchmode": ("AUTO", "EVENTS", "OFF"),
    "externallibrarycollectionmode": ("MIRROR", "SINGLE"),
    "externallibraryscanstatus": ("OK", "ERROR", "RUNNING", "PARTIAL"),
    "filerevisionstatus": ("KNOWN_GOOD", "NEEDS_TEST", "FAILED", "ARCHIVED"),
    "inboxsourcekind": ("URL", "BROWSER", "UPLOAD", "EXTERNAL"),
    "inboxitemstate": (
        "CAPTURED",
        "RESOLVING",
        "REVIEW",
        "IMPORTING",
        "COMPLETED",
        "FAILED",
        "DISMISSED",
    ),
    "notificationtarget": ("WEBHOOK", "DISCORD", "TELEGRAM", "NTFY"),
    "notificationeventtype": (
        "PRINT_COMPLETED",
        "PRINT_FAILED",
        "PRINT_CANCELLED",
        "PRINTER_OFFLINE",
    ),
    "notificationdeliverystatus": ("PENDING", "SENDING", "SENT", "FAILED"),
    "routingstrategy": ("MANUAL", "DEFAULT", "LEAST_BUSY"),
    "jobpriority": ("LOW", "NORMAL", "RUSH"),
    "compatibilitypolicy": ("SAFE", "ALLOW_MISMATCH"),
    "operatorgatestate": ("NOT_REQUIRED", "PENDING", "RELEASED", "HELD"),
    "materialslotstate": ("LOADED", "EMPTY", "UNKNOWN"),
    "materialsource": ("MANUAL", "BAMBU_AMS", "MOONRAKER_SPOOLMAN"),
    "printerprovider": (
        "MOONRAKER",
        "BAMBU_LAN",
        "PRUSALINK",
        "ELEGOO_CENTAURI",
        "OCTOPRINT",
    ),
    "vaultauditseverity": ("INFO", "WARNING", "CRITICAL"),
    "vaultauditfindingstate": ("OPEN", "RESOLVED", "IGNORED"),
    "vaultauditmode": ("QUICK", "FULL"),
    "vaultauditrunstate": (
        "PENDING",
        "RUNNING",
        "COMPLETED",
        "CANCELLED",
        "FAILED",
    ),
}


def _create_postgres_enum_types() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    bind = op.get_bind()
    for name, labels in _POSTGRES_ENUMS.items():
        sa.Enum(*labels, name=name).create(bind, checkfirst=True)


def _enum_cast(column: str, name: str) -> str | None:
    """Render the explicit PostgreSQL USING clause; SQLite ignores the kwarg."""
    if op.get_bind().dialect.name == "postgresql":
        return f'upper("{column}"::text)::{name}'
    return None


def _boolean_default(value: bool) -> sa.TextClause:
    if op.get_bind().dialect.name == "postgresql":
        return sa.text("true" if value else "false")
    return sa.text("1" if value else "0")


def _drop_unique_constraint_if_present(
    batch_op, table_name: str, constraint_name: str
) -> None:
    """Drop a chain-only unique constraint without breaking create-all installs."""
    bind = op.get_bind()
    if isinstance(bind, sa.engine.Connection) and bind.dialect.name == "postgresql":
        names = {
            item["name"] for item in sa.inspect(bind).get_unique_constraints(table_name)
        }
        if constraint_name not in names:
            return
    batch_op.drop_constraint(batch_op.f(constraint_name), type_="unique")


def _drop_index_if_present(batch_op, table_name: str, index_name: str) -> None:
    """Skip create-all-only index gaps on online PostgreSQL upgrades."""
    bind = op.get_bind()
    if isinstance(bind, sa.engine.Connection) and bind.dialect.name == "postgresql":
        names = {item["name"] for item in sa.inspect(bind).get_indexes(table_name)}
        if index_name not in names:
            return
    batch_op.drop_index(batch_op.f(index_name))


def _replace_postgres_named_constraint(
    batch_op,
    table_name: str,
    *,
    source_name: str,
    target_name: str,
    constraint_type: str,
    columns: tuple[str, ...] = (),
    condition: str | None = None,
    referred_table: str | None = None,
    referred_columns: tuple[str, ...] = (),
    ondelete: str | None = None,
) -> None:
    """Converge a released PostgreSQL constraint while keeping offline SQL useful."""
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    existing_names: set[str] | None = None
    existing: list[dict] | None = None
    if isinstance(bind, sa.engine.Connection):
        inspector = sa.inspect(bind)
        if constraint_type == "check":
            existing = inspector.get_check_constraints(table_name)
        elif constraint_type == "unique":
            existing = inspector.get_unique_constraints(table_name)
        else:
            existing = inspector.get_foreign_keys(table_name)
        existing_names = {item["name"] for item in existing if item.get("name")}

    if constraint_type == "foreignkey" and existing is not None:
        matching = [
            item
            for item in existing
            if tuple(item.get("constrained_columns") or ()) == columns
            and item.get("referred_table") == referred_table
            and tuple(item.get("referred_columns") or ()) == referred_columns
        ]
        if any(
            item.get("name") == target_name
            and (item.get("options") or {}).get("ondelete") == ondelete
            for item in matching
        ):
            return
        for item in matching:
            batch_op.drop_constraint(
                batch_op.f(item["name"]),
                type_="foreignkey",
            )
        batch_op.create_foreign_key(
            batch_op.f(target_name),
            referred_table,
            list(columns),
            list(referred_columns),
            ondelete=ondelete,
        )
        return

    if existing_names is None or source_name in existing_names:
        batch_op.drop_constraint(
            batch_op.f(source_name),
            type_=constraint_type,
        )
    if existing_names is not None and target_name in existing_names:
        return

    if constraint_type == "check":
        assert condition is not None
        batch_op.create_check_constraint(batch_op.f(target_name), condition)
    elif constraint_type == "unique":
        batch_op.create_unique_constraint(batch_op.f(target_name), list(columns))
    else:
        assert referred_table is not None
        batch_op.create_foreign_key(
            batch_op.f(target_name),
            referred_table,
            list(columns),
            list(referred_columns),
            ondelete=ondelete,
        )


def upgrade() -> None:
    """Upgrade schema."""
    _create_postgres_enum_types()
    # ### commands auto generated by Alembic - please adjust! ###
    with op.batch_alter_table(
        "artifact_material_requirements", schema=None
    ) as batch_op:
        batch_op.alter_column(
            "tool_index",
            existing_type=sa.INTEGER(),
            server_default=None,
            existing_nullable=False,
        )

    with op.batch_alter_table("artifact_provenance_links", schema=None) as batch_op:
        _drop_unique_constraint_if_present(
            batch_op,
            "artifact_provenance_links",
            "uq_artifact_provenance_links_import_key",
        )
        _drop_index_if_present(
            batch_op,
            "artifact_provenance_links",
            "ix_artifact_provenance_links_import_key",
        )
        batch_op.create_index(
            batch_op.f("ix_artifact_provenance_links_import_key"),
            ["import_key"],
            unique=True,
        )

    with op.batch_alter_table("audit_logs", schema=None) as batch_op:
        batch_op.alter_column(
            "diff_json",
            existing_type=sa.VARCHAR(),
            server_default=None,
            existing_nullable=False,
        )

    with op.batch_alter_table("background_jobs", schema=None) as batch_op:
        batch_op.alter_column(
            "visible",
            existing_type=sa.BOOLEAN(),
            server_default=None,
            existing_nullable=False,
        )
        batch_op.alter_column(
            "kind",
            existing_type=sa.VARCHAR(length=64),
            server_default=None,
            existing_nullable=False,
        )
        batch_op.alter_column(
            "state",
            existing_type=sa.VARCHAR(length=16),
            server_default=None,
            existing_nullable=False,
        )
        batch_op.alter_column(
            "status_json",
            existing_type=sa.TEXT(),
            server_default=None,
            existing_nullable=False,
        )
        batch_op.alter_column(
            "replay_safe",
            existing_type=sa.BOOLEAN(),
            server_default=None,
            existing_nullable=False,
        )
        batch_op.alter_column(
            "attempts",
            existing_type=sa.INTEGER(),
            server_default=None,
            existing_nullable=False,
        )

    with op.batch_alter_table("browser_devices", schema=None) as batch_op:
        _drop_unique_constraint_if_present(
            batch_op, "browser_devices", "uq_browser_devices_credential_hash"
        )
        _drop_index_if_present(
            batch_op, "browser_devices", "ix_browser_devices_credential_hash"
        )
        batch_op.create_index(
            batch_op.f("ix_browser_devices_credential_hash"),
            ["credential_hash"],
            unique=True,
        )

    with op.batch_alter_table("browser_pairing_codes", schema=None) as batch_op:
        _drop_unique_constraint_if_present(
            batch_op,
            "browser_pairing_codes",
            "uq_browser_pairing_codes_code_hash",
        )
        _drop_index_if_present(
            batch_op, "browser_pairing_codes", "ix_browser_pairing_codes_code_hash"
        )
        batch_op.create_index(
            batch_op.f("ix_browser_pairing_codes_code_hash"), ["code_hash"], unique=True
        )

    with op.batch_alter_table("capture_upload_slots", schema=None) as batch_op:
        batch_op.alter_column(
            "state",
            existing_type=sa.VARCHAR(length=16),
            type_=sa.Enum("PENDING", "UPLOADED", name="captureuploadslotstate"),
            existing_nullable=False,
            postgresql_using=_enum_cast("state", "captureuploadslotstate"),
        )
        _drop_index_if_present(
            batch_op, "capture_upload_slots", "ix_capture_upload_slots_inbox_item_id"
        )

    with op.batch_alter_table("documents", schema=None) as batch_op:
        batch_op.alter_column(
            "kind",
            existing_type=sa.VARCHAR(length=16),
            type_=sa.Enum("MARKDOWN", "PDF", "OTHER", name="documentkind"),
            existing_nullable=False,
            postgresql_using=_enum_cast("kind", "documentkind"),
        )

    with op.batch_alter_table("external_libraries", schema=None) as batch_op:
        batch_op.alter_column(
            "enabled",
            existing_type=sa.BOOLEAN(),
            server_default=None,
            existing_nullable=False,
        )
        batch_op.alter_column(
            "scan_interval_minutes",
            existing_type=sa.INTEGER(),
            server_default=None,
            existing_nullable=False,
        )
        batch_op.alter_column(
            "scan_schedule",
            existing_type=sa.VARCHAR(length=128),
            server_default=None,
            existing_nullable=False,
        )
        batch_op.alter_column(
            "watch_mode",
            existing_type=sa.VARCHAR(length=16),
            server_default=None,
            type_=sa.Enum("AUTO", "EVENTS", "OFF", name="externallibrarywatchmode"),
            existing_nullable=False,
            postgresql_using=_enum_cast("watch_mode", "externallibrarywatchmode"),
        )
        batch_op.alter_column(
            "collection_mode",
            existing_type=sa.VARCHAR(length=16),
            server_default=None,
            type_=sa.Enum("MIRROR", "SINGLE", name="externallibrarycollectionmode"),
            existing_nullable=False,
            postgresql_using=_enum_cast(
                "collection_mode", "externallibrarycollectionmode"
            ),
        )
        batch_op.alter_column(
            "last_scan_status",
            existing_type=sa.VARCHAR(length=16),
            type_=sa.Enum(
                "OK", "ERROR", "RUNNING", "PARTIAL", name="externallibraryscanstatus"
            ),
            existing_nullable=True,
            postgresql_using=_enum_cast(
                "last_scan_status", "externallibraryscanstatus"
            ),
        )

    with op.batch_alter_table("files", schema=None) as batch_op:
        batch_op.alter_column(
            "revision_status",
            existing_type=sa.VARCHAR(length=32),
            type_=sa.Enum(
                "KNOWN_GOOD",
                "NEEDS_TEST",
                "FAILED",
                "ARCHIVED",
                name="filerevisionstatus",
            ),
            existing_nullable=True,
            postgresql_using=_enum_cast("revision_status", "filerevisionstatus"),
        )
        batch_op.alter_column(
            "revision_notes",
            existing_type=sa.TEXT(),
            type_=sa.String(),
            existing_nullable=True,
        )
        batch_op.alter_column(
            "is_recommended",
            existing_type=sa.BOOLEAN(),
            server_default=None,
            existing_nullable=False,
        )
        batch_op.alter_column(
            "is_external",
            existing_type=sa.BOOLEAN(),
            server_default=None,
            existing_nullable=False,
        )

    with op.batch_alter_table("inbox_item_results", schema=None) as batch_op:
        batch_op.alter_column(
            "retryable",
            existing_type=sa.BOOLEAN(),
            server_default=None,
            existing_nullable=False,
        )

    with op.batch_alter_table("inbox_items", schema=None) as batch_op:
        batch_op.alter_column(
            "source_kind",
            existing_type=sa.VARCHAR(length=16),
            server_default=None,
            type_=sa.Enum(
                "URL", "BROWSER", "UPLOAD", "EXTERNAL", name="inboxsourcekind"
            ),
            existing_nullable=False,
            postgresql_using=_enum_cast("source_kind", "inboxsourcekind"),
        )
        batch_op.alter_column(
            "state",
            existing_type=sa.VARCHAR(length=16),
            server_default=None,
            type_=sa.Enum(
                "CAPTURED",
                "RESOLVING",
                "REVIEW",
                "IMPORTING",
                "COMPLETED",
                "FAILED",
                "DISMISSED",
                name="inboxitemstate",
            ),
            existing_nullable=False,
            postgresql_using=_enum_cast("state", "inboxitemstate"),
        )
        batch_op.alter_column(
            "manifest_json",
            existing_type=sa.TEXT(),
            server_default=None,
            existing_nullable=False,
        )
        batch_op.alter_column(
            "requested_tags_json",
            existing_type=sa.TEXT(),
            server_default=None,
            existing_nullable=False,
        )
        batch_op.alter_column(
            "retryable",
            existing_type=sa.BOOLEAN(),
            server_default=None,
            existing_nullable=False,
        )
        batch_op.alter_column(
            "attempt_count",
            existing_type=sa.INTEGER(),
            server_default=None,
            existing_nullable=False,
        )

    with op.batch_alter_table("metadata", schema=None) as batch_op:
        _drop_index_if_present(batch_op, "metadata", "ix_metadata_material_type")
        _drop_index_if_present(batch_op, "metadata", "ix_metadata_printer_model")
        _drop_index_if_present(batch_op, "metadata", "ix_metadata_slicer_name")

    with op.batch_alter_table("model_provenance_fields", schema=None) as batch_op:
        batch_op.alter_column(
            "user_override_set",
            existing_type=sa.BOOLEAN(),
            server_default=None,
            existing_nullable=False,
        )

    with op.batch_alter_table("model_provenance_sources", schema=None) as batch_op:
        batch_op.alter_column(
            "tags_json",
            existing_type=sa.TEXT(),
            server_default=None,
            existing_nullable=False,
        )

    with op.batch_alter_table("model_source_covers", schema=None) as batch_op:
        _drop_unique_constraint_if_present(
            batch_op,
            "model_source_covers",
            "uq_model_source_covers_provenance_source_id",
        )
        _drop_index_if_present(
            batch_op,
            "model_source_covers",
            "ix_model_source_covers_provenance_source_id",
        )
        batch_op.create_index(
            batch_op.f("ix_model_source_covers_provenance_source_id"),
            ["provenance_source_id"],
            unique=True,
        )

    with op.batch_alter_table("notification_channels", schema=None) as batch_op:
        batch_op.alter_column(
            "target",
            existing_type=sa.VARCHAR(length=32),
            type_=sa.Enum(
                "WEBHOOK", "DISCORD", "TELEGRAM", "NTFY", name="notificationtarget"
            ),
            existing_nullable=False,
            postgresql_using=_enum_cast("target", "notificationtarget"),
        )
        batch_op.alter_column(
            "enabled",
            existing_type=sa.BOOLEAN(),
            server_default=None,
            existing_nullable=False,
        )
        batch_op.alter_column(
            "config_json",
            existing_type=sa.TEXT(),
            server_default=None,
            existing_nullable=False,
        )
        batch_op.alter_column(
            "events_json", existing_type=sa.TEXT(), server_default=None, nullable=True
        )
        batch_op.alter_column(
            "consecutive_failures",
            existing_type=sa.INTEGER(),
            server_default=None,
            existing_nullable=False,
        )

    with op.batch_alter_table("notification_deliveries", schema=None) as batch_op:
        batch_op.alter_column(
            "event_type",
            existing_type=sa.VARCHAR(length=32),
            type_=sa.Enum(
                "PRINT_COMPLETED",
                "PRINT_FAILED",
                "PRINT_CANCELLED",
                "PRINTER_OFFLINE",
                name="notificationeventtype",
            ),
            existing_nullable=False,
            postgresql_using=_enum_cast("event_type", "notificationeventtype"),
        )
        batch_op.alter_column(
            "context_json", existing_type=sa.TEXT(), server_default=None, nullable=True
        )
        batch_op.alter_column(
            "status",
            existing_type=sa.VARCHAR(length=16),
            type_=sa.Enum(
                "PENDING",
                "SENDING",
                "SENT",
                "FAILED",
                name="notificationdeliverystatus",
            ),
            existing_nullable=False,
            postgresql_using=_enum_cast("status", "notificationdeliverystatus"),
        )
        batch_op.alter_column(
            "attempts",
            existing_type=sa.INTEGER(),
            server_default=None,
            existing_nullable=False,
        )

    with op.batch_alter_table("print_batches", schema=None) as batch_op:
        _replace_postgres_named_constraint(
            batch_op,
            "print_batches",
            source_name="ck_print_batches_quantity_positive",
            target_name="ck_print_batches_ck_print_batches_quantity_positive",
            constraint_type="check",
            condition="quantity > 0",
        )
        batch_op.alter_column(
            "routing_strategy",
            existing_type=sa.VARCHAR(length=16),
            type_=sa.Enum("MANUAL", "DEFAULT", "LEAST_BUSY", name="routingstrategy"),
            existing_nullable=False,
            existing_server_default=sa.text("'LEAST_BUSY'"),
            postgresql_using=_enum_cast("routing_strategy", "routingstrategy"),
        )
        batch_op.alter_column(
            "priority",
            existing_type=sa.VARCHAR(length=16),
            type_=sa.Enum("LOW", "NORMAL", "RUSH", name="jobpriority"),
            existing_nullable=False,
            existing_server_default=sa.text("'NORMAL'"),
            postgresql_using=_enum_cast("priority", "jobpriority"),
        )
        batch_op.alter_column(
            "compatibility_policy",
            existing_type=sa.VARCHAR(length=32),
            type_=sa.Enum("SAFE", "ALLOW_MISMATCH", name="compatibilitypolicy"),
            existing_nullable=False,
            existing_server_default=sa.text("'SAFE'"),
            postgresql_using=_enum_cast("compatibility_policy", "compatibilitypolicy"),
        )

    with op.batch_alter_table("print_jobs", schema=None) as batch_op:
        batch_op.alter_column(
            "routing_strategy",
            existing_type=sa.VARCHAR(length=16),
            type_=sa.Enum("MANUAL", "DEFAULT", "LEAST_BUSY", name="routingstrategy"),
            existing_nullable=False,
            existing_server_default=sa.text("'MANUAL'"),
            postgresql_using=_enum_cast("routing_strategy", "routingstrategy"),
        )
        batch_op.alter_column(
            "priority",
            existing_type=sa.VARCHAR(length=16),
            type_=sa.Enum("LOW", "NORMAL", "RUSH", name="jobpriority"),
            existing_nullable=False,
            existing_server_default=sa.text("'NORMAL'"),
            postgresql_using=_enum_cast("priority", "jobpriority"),
        )
        batch_op.alter_column(
            "compatibility_policy",
            existing_type=sa.VARCHAR(length=32),
            type_=sa.Enum("SAFE", "ALLOW_MISMATCH", name="compatibilitypolicy"),
            existing_nullable=False,
            existing_server_default=sa.text("'SAFE'"),
            postgresql_using=_enum_cast("compatibility_policy", "compatibilitypolicy"),
        )
        batch_op.alter_column(
            "operator_gate_state",
            existing_type=sa.VARCHAR(length=24),
            type_=sa.Enum(
                "NOT_REQUIRED", "PENDING", "RELEASED", "HELD", name="operatorgatestate"
            ),
            existing_nullable=False,
            existing_server_default=sa.text("'NOT_REQUIRED'"),
            postgresql_using=_enum_cast("operator_gate_state", "operatorgatestate"),
        )
        batch_op.alter_column(
            "artifact_evidence",
            existing_type=sa.VARCHAR(length=32),
            server_default=None,
            existing_nullable=False,
        )
        _drop_index_if_present(batch_op, "print_jobs", "ix_print_jobs_model_state")
        _drop_index_if_present(batch_op, "print_jobs", "ix_print_jobs_printer_state")
        _drop_index_if_present(
            batch_op, "print_jobs", "ix_print_jobs_state_queue_position"
        )
        _drop_unique_constraint_if_present(
            batch_op, "print_jobs", "uq_print_jobs_printer_provider_job"
        )

    with op.batch_alter_table("printer_files", schema=None) as batch_op:
        batch_op.alter_column(
            "matched_by",
            existing_type=sa.VARCHAR(length=32),
            server_default=None,
            existing_nullable=False,
        )
        _drop_unique_constraint_if_present(
            batch_op, "printer_files", "uq_printer_files_printer_remote"
        )

    with op.batch_alter_table("printer_material_slots", schema=None) as batch_op:
        batch_op.alter_column(
            "state",
            existing_type=sa.VARCHAR(length=16),
            type_=sa.Enum("LOADED", "EMPTY", "UNKNOWN", name="materialslotstate"),
            existing_nullable=False,
            existing_server_default=sa.text("'UNKNOWN'"),
            postgresql_using=_enum_cast("state", "materialslotstate"),
        )
        batch_op.alter_column(
            "source",
            existing_type=sa.VARCHAR(length=32),
            type_=sa.Enum(
                "MANUAL", "BAMBU_AMS", "MOONRAKER_SPOOLMAN", name="materialsource"
            ),
            existing_nullable=False,
            existing_server_default=sa.text("'MANUAL'"),
            postgresql_using=_enum_cast("source", "materialsource"),
        )

    with op.batch_alter_table("printer_tools", schema=None) as batch_op:
        batch_op.alter_column(
            "tool_key",
            existing_type=sa.VARCHAR(length=64),
            server_default=None,
            existing_nullable=False,
        )
        batch_op.alter_column(
            "label",
            existing_type=sa.VARCHAR(length=128),
            server_default=None,
            existing_nullable=False,
        )
        batch_op.alter_column(
            "source",
            existing_type=sa.VARCHAR(length=32),
            type_=sa.Enum(
                "MANUAL", "BAMBU_AMS", "MOONRAKER_SPOOLMAN", name="materialsource"
            ),
            existing_nullable=False,
            existing_server_default=sa.text("'MANUAL'"),
            postgresql_using=_enum_cast("source", "materialsource"),
        )

    with op.batch_alter_table("printers", schema=None) as batch_op:
        batch_op.alter_column(
            "provider",
            existing_type=sa.VARCHAR(length=32),
            server_default=None,
            type_=sa.Enum(
                "MOONRAKER",
                "BAMBU_LAN",
                "PRUSALINK",
                "ELEGOO_CENTAURI",
                "OCTOPRINT",
                name="printerprovider",
            ),
            existing_nullable=False,
            postgresql_using=_enum_cast("provider", "printerprovider"),
        )
        batch_op.alter_column(
            "moonraker_url",
            existing_type=sa.VARCHAR(length=512),
            server_default=None,
            existing_nullable=False,
        )

    with op.batch_alter_table("provider_oauth_states", schema=None) as batch_op:
        _drop_unique_constraint_if_present(
            batch_op,
            "provider_oauth_states",
            "uq_provider_oauth_states_state_hash",
        )
        _drop_index_if_present(
            batch_op, "provider_oauth_states", "ix_provider_oauth_states_state_hash"
        )
        batch_op.create_index(
            batch_op.f("ix_provider_oauth_states_state_hash"),
            ["state_hash"],
            unique=True,
        )

    with op.batch_alter_table("share_links", schema=None) as batch_op:
        batch_op.alter_column(
            "allow_download",
            existing_type=sa.BOOLEAN(),
            server_default=None,
            existing_nullable=False,
        )
        batch_op.alter_column(
            "access_count",
            existing_type=sa.INTEGER(),
            server_default=None,
            existing_nullable=False,
        )

    with op.batch_alter_table("staging_leases", schema=None) as batch_op:
        _replace_postgres_named_constraint(
            batch_op,
            "staging_leases",
            source_name="staging_leases_path_key",
            target_name="uq_staging_leases_path",
            constraint_type="unique",
            columns=("path",),
        )
        _drop_unique_constraint_if_present(
            batch_op,
            "staging_leases",
            "uq_staging_leases_capture_upload_slot_id",
        )
        _drop_unique_constraint_if_present(
            batch_op, "staging_leases", "uq_staging_leases_inbox_item_id"
        )
        _drop_unique_constraint_if_present(
            batch_op,
            "staging_leases",
            "uq_staging_leases_model_source_cover_id",
        )
        _drop_index_if_present(
            batch_op,
            "staging_leases",
            "ix_staging_leases_capture_upload_slot_id",
        )
        batch_op.create_index(
            batch_op.f("ix_staging_leases_capture_upload_slot_id"),
            ["capture_upload_slot_id"],
            unique=True,
        )
        _drop_index_if_present(
            batch_op, "staging_leases", "ix_staging_leases_inbox_item_id"
        )
        batch_op.create_index(
            batch_op.f("ix_staging_leases_inbox_item_id"),
            ["inbox_item_id"],
            unique=True,
        )
        _drop_index_if_present(
            batch_op,
            "staging_leases",
            "ix_staging_leases_model_source_cover_id",
        )
        batch_op.create_index(
            batch_op.f("ix_staging_leases_model_source_cover_id"),
            ["model_source_cover_id"],
            unique=True,
        )

    with op.batch_alter_table("system_config", schema=None) as batch_op:
        batch_op.alter_column(
            "auto_mark_known_good",
            existing_type=sa.BOOLEAN(),
            server_default=None,
            existing_nullable=False,
        )
        batch_op.alter_column(
            "external_libraries_enabled",
            existing_type=sa.BOOLEAN(),
            server_default=None,
            existing_nullable=False,
        )
        batch_op.alter_column(
            "notifications_enabled",
            existing_type=sa.BOOLEAN(),
            server_default=None,
            existing_nullable=False,
        )
        batch_op.alter_column(
            "spoolman_enabled",
            existing_type=sa.BOOLEAN(),
            server_default=None,
            existing_nullable=False,
        )
        batch_op.alter_column(
            "spoolman_write_enabled",
            existing_type=sa.BOOLEAN(),
            server_default=None,
            existing_nullable=False,
        )
        batch_op.alter_column(
            "spoolman_write_force",
            existing_type=sa.BOOLEAN(),
            server_default=None,
            existing_nullable=False,
        )

    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.alter_column(
            "auth_version",
            existing_type=sa.INTEGER(),
            server_default=None,
            existing_nullable=False,
        )

    with op.batch_alter_table("vault_audit_findings", schema=None) as batch_op:
        _replace_postgres_named_constraint(
            batch_op,
            "vault_audit_findings",
            source_name="vault_audit_findings_run_id_fkey",
            target_name="fk_vault_audit_findings_run_id_vault_audit_runs",
            constraint_type="foreignkey",
            columns=("run_id",),
            referred_table="vault_audit_runs",
            referred_columns=("id",),
            ondelete="CASCADE",
        )
        batch_op.alter_column(
            "severity",
            existing_type=sa.VARCHAR(length=16),
            type_=sa.Enum("INFO", "WARNING", "CRITICAL", name="vaultauditseverity"),
            existing_nullable=False,
            postgresql_using=_enum_cast("severity", "vaultauditseverity"),
        )
        batch_op.alter_column(
            "state",
            existing_type=sa.VARCHAR(length=16),
            server_default=None,
            type_=sa.Enum("OPEN", "RESOLVED", "IGNORED", name="vaultauditfindingstate"),
            existing_nullable=False,
            postgresql_using=_enum_cast("state", "vaultauditfindingstate"),
        )
        batch_op.alter_column(
            "details_json",
            existing_type=sa.TEXT(),
            server_default=None,
            existing_nullable=False,
        )

    with op.batch_alter_table("vault_audit_runs", schema=None) as batch_op:
        batch_op.alter_column(
            "mode",
            existing_type=sa.VARCHAR(length=16),
            type_=sa.Enum("QUICK", "FULL", name="vaultauditmode"),
            existing_nullable=False,
            postgresql_using=_enum_cast("mode", "vaultauditmode"),
        )
        batch_op.alter_column(
            "state",
            existing_type=sa.VARCHAR(length=16),
            server_default=None,
            type_=sa.Enum(
                "PENDING",
                "RUNNING",
                "COMPLETED",
                "CANCELLED",
                "FAILED",
                name="vaultauditrunstate",
            ),
            existing_nullable=False,
            postgresql_using=_enum_cast("state", "vaultauditrunstate"),
        )
        batch_op.alter_column(
            "info_count",
            existing_type=sa.INTEGER(),
            server_default=None,
            existing_nullable=False,
        )
        batch_op.alter_column(
            "warning_count",
            existing_type=sa.INTEGER(),
            server_default=None,
            existing_nullable=False,
        )
        batch_op.alter_column(
            "critical_count",
            existing_type=sa.INTEGER(),
            server_default=None,
            existing_nullable=False,
        )
        batch_op.alter_column(
            "progress",
            existing_type=sa.FLOAT(),
            server_default=None,
            existing_nullable=False,
        )
        batch_op.alter_column(
            "cancel_requested",
            existing_type=sa.BOOLEAN(),
            server_default=None,
            existing_nullable=False,
        )

    # ### end Alembic commands ###


def downgrade() -> None:
    """Downgrade schema."""
    # ### commands auto generated by Alembic - please adjust! ###
    with op.batch_alter_table("vault_audit_runs", schema=None) as batch_op:
        batch_op.alter_column(
            "cancel_requested",
            existing_type=sa.BOOLEAN(),
            server_default=_boolean_default(False),
            existing_nullable=False,
        )
        batch_op.alter_column(
            "progress",
            existing_type=sa.FLOAT(),
            server_default=sa.text("'0'"),
            existing_nullable=False,
        )
        batch_op.alter_column(
            "critical_count",
            existing_type=sa.INTEGER(),
            server_default=sa.text("'0'"),
            existing_nullable=False,
        )
        batch_op.alter_column(
            "warning_count",
            existing_type=sa.INTEGER(),
            server_default=sa.text("'0'"),
            existing_nullable=False,
        )
        batch_op.alter_column(
            "info_count",
            existing_type=sa.INTEGER(),
            server_default=sa.text("'0'"),
            existing_nullable=False,
        )
        batch_op.alter_column(
            "state",
            existing_type=sa.Enum(
                "PENDING",
                "RUNNING",
                "COMPLETED",
                "CANCELLED",
                "FAILED",
                name="vaultauditrunstate",
            ),
            server_default=sa.text("'PENDING'"),
            type_=sa.VARCHAR(length=16),
            existing_nullable=False,
        )
        batch_op.alter_column(
            "mode",
            existing_type=sa.Enum("QUICK", "FULL", name="vaultauditmode"),
            type_=sa.VARCHAR(length=16),
            existing_nullable=False,
        )

    with op.batch_alter_table("vault_audit_findings", schema=None) as batch_op:
        batch_op.alter_column(
            "details_json",
            existing_type=sa.TEXT(),
            server_default=sa.text("'{}'"),
            existing_nullable=False,
        )
        batch_op.alter_column(
            "state",
            existing_type=sa.Enum(
                "OPEN", "RESOLVED", "IGNORED", name="vaultauditfindingstate"
            ),
            server_default=sa.text("'OPEN'"),
            type_=sa.VARCHAR(length=16),
            existing_nullable=False,
        )
        batch_op.alter_column(
            "severity",
            existing_type=sa.Enum(
                "INFO", "WARNING", "CRITICAL", name="vaultauditseverity"
            ),
            type_=sa.VARCHAR(length=16),
            existing_nullable=False,
        )
        _replace_postgres_named_constraint(
            batch_op,
            "vault_audit_findings",
            source_name="fk_vault_audit_findings_run_id_vault_audit_runs",
            target_name="vault_audit_findings_run_id_fkey",
            constraint_type="foreignkey",
            columns=("run_id",),
            referred_table="vault_audit_runs",
            referred_columns=("id",),
        )

    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.alter_column(
            "auth_version",
            existing_type=sa.INTEGER(),
            server_default=sa.text("'0'"),
            existing_nullable=False,
        )

    with op.batch_alter_table("system_config", schema=None) as batch_op:
        batch_op.alter_column(
            "spoolman_write_force",
            existing_type=sa.BOOLEAN(),
            server_default=_boolean_default(False),
            existing_nullable=False,
        )
        batch_op.alter_column(
            "spoolman_write_enabled",
            existing_type=sa.BOOLEAN(),
            server_default=_boolean_default(True),
            existing_nullable=False,
        )
        batch_op.alter_column(
            "spoolman_enabled",
            existing_type=sa.BOOLEAN(),
            server_default=_boolean_default(False),
            existing_nullable=False,
        )
        batch_op.alter_column(
            "notifications_enabled",
            existing_type=sa.BOOLEAN(),
            server_default=_boolean_default(False),
            existing_nullable=False,
        )
        batch_op.alter_column(
            "external_libraries_enabled",
            existing_type=sa.BOOLEAN(),
            server_default=_boolean_default(False),
            existing_nullable=False,
        )
        batch_op.alter_column(
            "auto_mark_known_good",
            existing_type=sa.BOOLEAN(),
            server_default=_boolean_default(True),
            existing_nullable=False,
        )

    with op.batch_alter_table("staging_leases", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_staging_leases_model_source_cover_id"))
        batch_op.create_index(
            batch_op.f("ix_staging_leases_model_source_cover_id"),
            ["model_source_cover_id"],
            unique=False,
        )
        batch_op.drop_index(batch_op.f("ix_staging_leases_inbox_item_id"))
        batch_op.create_index(
            batch_op.f("ix_staging_leases_inbox_item_id"),
            ["inbox_item_id"],
            unique=False,
        )
        batch_op.drop_index(batch_op.f("ix_staging_leases_capture_upload_slot_id"))
        batch_op.create_index(
            batch_op.f("ix_staging_leases_capture_upload_slot_id"),
            ["capture_upload_slot_id"],
            unique=False,
        )
        batch_op.create_unique_constraint(
            batch_op.f("uq_staging_leases_model_source_cover_id"),
            ["model_source_cover_id"],
        )
        batch_op.create_unique_constraint(
            batch_op.f("uq_staging_leases_inbox_item_id"), ["inbox_item_id"]
        )
        batch_op.create_unique_constraint(
            batch_op.f("uq_staging_leases_capture_upload_slot_id"),
            ["capture_upload_slot_id"],
        )
        _replace_postgres_named_constraint(
            batch_op,
            "staging_leases",
            source_name="uq_staging_leases_path",
            target_name="staging_leases_path_key",
            constraint_type="unique",
            columns=("path",),
        )

    with op.batch_alter_table("share_links", schema=None) as batch_op:
        batch_op.alter_column(
            "access_count",
            existing_type=sa.INTEGER(),
            server_default=sa.text("'0'"),
            existing_nullable=False,
        )
        batch_op.alter_column(
            "allow_download",
            existing_type=sa.BOOLEAN(),
            server_default=_boolean_default(False),
            existing_nullable=False,
        )

    with op.batch_alter_table("provider_oauth_states", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_provider_oauth_states_state_hash"))
        batch_op.create_index(
            batch_op.f("ix_provider_oauth_states_state_hash"),
            ["state_hash"],
            unique=False,
        )
        batch_op.create_unique_constraint(
            batch_op.f("uq_provider_oauth_states_state_hash"), ["state_hash"]
        )

    with op.batch_alter_table("printers", schema=None) as batch_op:
        batch_op.alter_column(
            "moonraker_url",
            existing_type=sa.VARCHAR(length=512),
            server_default=sa.text("('')"),
            existing_nullable=False,
        )
        batch_op.alter_column(
            "provider",
            existing_type=sa.Enum(
                "MOONRAKER",
                "BAMBU_LAN",
                "PRUSALINK",
                "ELEGOO_CENTAURI",
                "OCTOPRINT",
                name="printerprovider",
            ),
            server_default=sa.text("'moonraker'"),
            type_=sa.VARCHAR(length=32),
            existing_nullable=False,
        )

    with op.batch_alter_table("printer_tools", schema=None) as batch_op:
        batch_op.alter_column(
            "source",
            existing_type=sa.Enum(
                "MANUAL", "BAMBU_AMS", "MOONRAKER_SPOOLMAN", name="materialsource"
            ),
            type_=sa.VARCHAR(length=32),
            existing_nullable=False,
            existing_server_default=sa.text("'MANUAL'"),
        )
        batch_op.alter_column(
            "label",
            existing_type=sa.VARCHAR(length=128),
            server_default=sa.text("'Tool 0'"),
            existing_nullable=False,
        )
        batch_op.alter_column(
            "tool_key",
            existing_type=sa.VARCHAR(length=64),
            server_default=sa.text("'tool0'"),
            existing_nullable=False,
        )

    with op.batch_alter_table("printer_material_slots", schema=None) as batch_op:
        batch_op.alter_column(
            "source",
            existing_type=sa.Enum(
                "MANUAL", "BAMBU_AMS", "MOONRAKER_SPOOLMAN", name="materialsource"
            ),
            type_=sa.VARCHAR(length=32),
            existing_nullable=False,
            existing_server_default=sa.text("'MANUAL'"),
        )
        batch_op.alter_column(
            "state",
            existing_type=sa.Enum(
                "LOADED", "EMPTY", "UNKNOWN", name="materialslotstate"
            ),
            type_=sa.VARCHAR(length=16),
            existing_nullable=False,
            existing_server_default=sa.text("'UNKNOWN'"),
        )

    with op.batch_alter_table("printer_files", schema=None) as batch_op:
        batch_op.create_unique_constraint(
            batch_op.f("uq_printer_files_printer_remote"),
            ["printer_id", "remote_filename"],
        )
        batch_op.alter_column(
            "matched_by",
            existing_type=sa.VARCHAR(length=32),
            server_default=sa.text("'external'"),
            existing_nullable=False,
        )

    with op.batch_alter_table("print_jobs", schema=None) as batch_op:
        batch_op.create_unique_constraint(
            batch_op.f("uq_print_jobs_printer_provider_job"),
            ["printer_id", "provider_job_id"],
        )
        batch_op.create_index(
            batch_op.f("ix_print_jobs_state_queue_position"),
            ["state", "queue_position"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_print_jobs_printer_state"),
            ["printer_id", "state"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_print_jobs_model_state"), ["model_id", "state"], unique=False
        )
        batch_op.alter_column(
            "artifact_evidence",
            existing_type=sa.VARCHAR(length=32),
            server_default=sa.text("'vault'"),
            existing_nullable=False,
        )
        batch_op.alter_column(
            "operator_gate_state",
            existing_type=sa.Enum(
                "NOT_REQUIRED", "PENDING", "RELEASED", "HELD", name="operatorgatestate"
            ),
            type_=sa.VARCHAR(length=24),
            existing_nullable=False,
            existing_server_default=sa.text("'NOT_REQUIRED'"),
        )
        batch_op.alter_column(
            "compatibility_policy",
            existing_type=sa.Enum("SAFE", "ALLOW_MISMATCH", name="compatibilitypolicy"),
            type_=sa.VARCHAR(length=32),
            existing_nullable=False,
            existing_server_default=sa.text("'SAFE'"),
        )
        batch_op.alter_column(
            "priority",
            existing_type=sa.Enum("LOW", "NORMAL", "RUSH", name="jobpriority"),
            type_=sa.VARCHAR(length=16),
            existing_nullable=False,
            existing_server_default=sa.text("'NORMAL'"),
        )
        batch_op.alter_column(
            "routing_strategy",
            existing_type=sa.Enum(
                "MANUAL", "DEFAULT", "LEAST_BUSY", name="routingstrategy"
            ),
            type_=sa.VARCHAR(length=16),
            existing_nullable=False,
            existing_server_default=sa.text("'MANUAL'"),
        )

    with op.batch_alter_table("print_batches", schema=None) as batch_op:
        batch_op.alter_column(
            "compatibility_policy",
            existing_type=sa.Enum("SAFE", "ALLOW_MISMATCH", name="compatibilitypolicy"),
            type_=sa.VARCHAR(length=32),
            existing_nullable=False,
            existing_server_default=sa.text("'SAFE'"),
        )
        batch_op.alter_column(
            "priority",
            existing_type=sa.Enum("LOW", "NORMAL", "RUSH", name="jobpriority"),
            type_=sa.VARCHAR(length=16),
            existing_nullable=False,
            existing_server_default=sa.text("'NORMAL'"),
        )
        batch_op.alter_column(
            "routing_strategy",
            existing_type=sa.Enum(
                "MANUAL", "DEFAULT", "LEAST_BUSY", name="routingstrategy"
            ),
            type_=sa.VARCHAR(length=16),
            existing_nullable=False,
            existing_server_default=sa.text("'LEAST_BUSY'"),
        )
        _replace_postgres_named_constraint(
            batch_op,
            "print_batches",
            source_name="ck_print_batches_ck_print_batches_quantity_positive",
            target_name="ck_print_batches_quantity_positive",
            constraint_type="check",
            condition="quantity > 0",
        )

    with op.batch_alter_table("notification_deliveries", schema=None) as batch_op:
        batch_op.alter_column(
            "attempts",
            existing_type=sa.INTEGER(),
            server_default=sa.text("'0'"),
            existing_nullable=False,
        )
        batch_op.alter_column(
            "status",
            existing_type=sa.Enum(
                "PENDING",
                "SENDING",
                "SENT",
                "FAILED",
                name="notificationdeliverystatus",
            ),
            type_=sa.VARCHAR(length=16),
            existing_nullable=False,
        )
        batch_op.alter_column(
            "context_json",
            existing_type=sa.TEXT(),
            server_default=sa.text("'{}'"),
            nullable=False,
        )
        batch_op.alter_column(
            "event_type",
            existing_type=sa.Enum(
                "PRINT_COMPLETED",
                "PRINT_FAILED",
                "PRINT_CANCELLED",
                "PRINTER_OFFLINE",
                name="notificationeventtype",
            ),
            type_=sa.VARCHAR(length=32),
            existing_nullable=False,
        )

    with op.batch_alter_table("notification_channels", schema=None) as batch_op:
        batch_op.alter_column(
            "consecutive_failures",
            existing_type=sa.INTEGER(),
            server_default=sa.text("'0'"),
            existing_nullable=False,
        )
        batch_op.alter_column(
            "events_json",
            existing_type=sa.TEXT(),
            server_default=sa.text("'[]'"),
            nullable=False,
        )
        batch_op.alter_column(
            "config_json",
            existing_type=sa.TEXT(),
            server_default=sa.text("'{}'"),
            existing_nullable=False,
        )
        batch_op.alter_column(
            "enabled",
            existing_type=sa.BOOLEAN(),
            server_default=_boolean_default(True),
            existing_nullable=False,
        )
        batch_op.alter_column(
            "target",
            existing_type=sa.Enum(
                "WEBHOOK", "DISCORD", "TELEGRAM", "NTFY", name="notificationtarget"
            ),
            type_=sa.VARCHAR(length=32),
            existing_nullable=False,
        )

    with op.batch_alter_table("model_source_covers", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_model_source_covers_provenance_source_id"))
        batch_op.create_index(
            batch_op.f("ix_model_source_covers_provenance_source_id"),
            ["provenance_source_id"],
            unique=False,
        )
        batch_op.create_unique_constraint(
            batch_op.f("uq_model_source_covers_provenance_source_id"),
            ["provenance_source_id"],
        )

    with op.batch_alter_table("model_provenance_sources", schema=None) as batch_op:
        batch_op.alter_column(
            "tags_json",
            existing_type=sa.TEXT(),
            server_default=sa.text("'[]'"),
            existing_nullable=False,
        )

    with op.batch_alter_table("model_provenance_fields", schema=None) as batch_op:
        batch_op.alter_column(
            "user_override_set",
            existing_type=sa.BOOLEAN(),
            server_default=_boolean_default(False),
            existing_nullable=False,
        )

    with op.batch_alter_table("metadata", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_metadata_slicer_name"), ["slicer_name"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_metadata_printer_model"), ["printer_model"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_metadata_material_type"), ["material_type"], unique=False
        )

    with op.batch_alter_table("inbox_items", schema=None) as batch_op:
        batch_op.alter_column(
            "attempt_count",
            existing_type=sa.INTEGER(),
            server_default=sa.text("'0'"),
            existing_nullable=False,
        )
        batch_op.alter_column(
            "retryable",
            existing_type=sa.BOOLEAN(),
            server_default=_boolean_default(False),
            existing_nullable=False,
        )
        batch_op.alter_column(
            "requested_tags_json",
            existing_type=sa.TEXT(),
            server_default=sa.text("'[]'"),
            existing_nullable=False,
        )
        batch_op.alter_column(
            "manifest_json",
            existing_type=sa.TEXT(),
            server_default=sa.text("'{}'"),
            existing_nullable=False,
        )
        batch_op.alter_column(
            "state",
            existing_type=sa.Enum(
                "CAPTURED",
                "RESOLVING",
                "REVIEW",
                "IMPORTING",
                "COMPLETED",
                "FAILED",
                "DISMISSED",
                name="inboxitemstate",
            ),
            server_default=sa.text("'CAPTURED'"),
            type_=sa.VARCHAR(length=16),
            existing_nullable=False,
        )
        batch_op.alter_column(
            "source_kind",
            existing_type=sa.Enum(
                "URL", "BROWSER", "UPLOAD", "EXTERNAL", name="inboxsourcekind"
            ),
            server_default=sa.text("'URL'"),
            type_=sa.VARCHAR(length=16),
            existing_nullable=False,
        )

    with op.batch_alter_table("inbox_item_results", schema=None) as batch_op:
        batch_op.alter_column(
            "retryable",
            existing_type=sa.BOOLEAN(),
            server_default=_boolean_default(False),
            existing_nullable=False,
        )

    with op.batch_alter_table("files", schema=None) as batch_op:
        batch_op.alter_column(
            "is_external",
            existing_type=sa.BOOLEAN(),
            server_default=_boolean_default(False),
            existing_nullable=False,
        )
        batch_op.alter_column(
            "is_recommended",
            existing_type=sa.BOOLEAN(),
            server_default=_boolean_default(False),
            existing_nullable=False,
        )
        batch_op.alter_column(
            "revision_notes",
            existing_type=sa.String(),
            type_=sa.TEXT(),
            existing_nullable=True,
        )
        batch_op.alter_column(
            "revision_status",
            existing_type=sa.Enum(
                "KNOWN_GOOD",
                "NEEDS_TEST",
                "FAILED",
                "ARCHIVED",
                name="filerevisionstatus",
            ),
            type_=sa.VARCHAR(length=32),
            existing_nullable=True,
        )

    with op.batch_alter_table("external_libraries", schema=None) as batch_op:
        batch_op.alter_column(
            "last_scan_status",
            existing_type=sa.Enum(
                "OK", "ERROR", "RUNNING", "PARTIAL", name="externallibraryscanstatus"
            ),
            type_=sa.VARCHAR(length=16),
            existing_nullable=True,
        )
        batch_op.alter_column(
            "collection_mode",
            existing_type=sa.Enum(
                "MIRROR", "SINGLE", name="externallibrarycollectionmode"
            ),
            server_default=sa.text("'mirror'"),
            type_=sa.VARCHAR(length=16),
            existing_nullable=False,
        )
        batch_op.alter_column(
            "watch_mode",
            existing_type=sa.Enum(
                "AUTO", "EVENTS", "OFF", name="externallibrarywatchmode"
            ),
            server_default=sa.text("'AUTO'"),
            type_=sa.VARCHAR(length=16),
            existing_nullable=False,
        )
        batch_op.alter_column(
            "scan_schedule",
            existing_type=sa.VARCHAR(length=128),
            server_default=sa.text("'0 * * * *'"),
            existing_nullable=False,
        )
        batch_op.alter_column(
            "scan_interval_minutes",
            existing_type=sa.INTEGER(),
            server_default=sa.text("'60'"),
            existing_nullable=False,
        )
        batch_op.alter_column(
            "enabled",
            existing_type=sa.BOOLEAN(),
            server_default=_boolean_default(True),
            existing_nullable=False,
        )

    with op.batch_alter_table("documents", schema=None) as batch_op:
        batch_op.alter_column(
            "kind",
            existing_type=sa.Enum("MARKDOWN", "PDF", "OTHER", name="documentkind"),
            type_=sa.VARCHAR(length=16),
            existing_nullable=False,
        )

    with op.batch_alter_table("capture_upload_slots", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_capture_upload_slots_inbox_item_id"),
            ["inbox_item_id"],
            unique=False,
        )
        batch_op.alter_column(
            "state",
            existing_type=sa.Enum("PENDING", "UPLOADED", name="captureuploadslotstate"),
            type_=sa.VARCHAR(length=16),
            existing_nullable=False,
        )

    with op.batch_alter_table("browser_pairing_codes", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_browser_pairing_codes_code_hash"))
        batch_op.create_index(
            batch_op.f("ix_browser_pairing_codes_code_hash"),
            ["code_hash"],
            unique=False,
        )
        batch_op.create_unique_constraint(
            batch_op.f("uq_browser_pairing_codes_code_hash"), ["code_hash"]
        )

    with op.batch_alter_table("browser_devices", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_browser_devices_credential_hash"))
        batch_op.create_index(
            batch_op.f("ix_browser_devices_credential_hash"),
            ["credential_hash"],
            unique=False,
        )
        batch_op.create_unique_constraint(
            batch_op.f("uq_browser_devices_credential_hash"), ["credential_hash"]
        )

    with op.batch_alter_table("background_jobs", schema=None) as batch_op:
        batch_op.alter_column(
            "attempts",
            existing_type=sa.INTEGER(),
            server_default=sa.text("'0'"),
            existing_nullable=False,
        )
        batch_op.alter_column(
            "replay_safe",
            existing_type=sa.BOOLEAN(),
            server_default=_boolean_default(False),
            existing_nullable=False,
        )
        batch_op.alter_column(
            "status_json",
            existing_type=sa.TEXT(),
            server_default=sa.text("'{}'"),
            existing_nullable=False,
        )
        batch_op.alter_column(
            "state",
            existing_type=sa.VARCHAR(length=16),
            server_default=sa.text("'pending'"),
            existing_nullable=False,
        )
        batch_op.alter_column(
            "kind",
            existing_type=sa.VARCHAR(length=64),
            server_default=sa.text("'generic'"),
            existing_nullable=False,
        )
        batch_op.alter_column(
            "visible",
            existing_type=sa.BOOLEAN(),
            server_default=_boolean_default(True),
            existing_nullable=False,
        )

    with op.batch_alter_table("audit_logs", schema=None) as batch_op:
        batch_op.alter_column(
            "diff_json",
            existing_type=sa.VARCHAR(),
            server_default=sa.text("'{}'"),
            existing_nullable=False,
        )

    with op.batch_alter_table("artifact_provenance_links", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_artifact_provenance_links_import_key"))
        batch_op.create_index(
            batch_op.f("ix_artifact_provenance_links_import_key"),
            ["import_key"],
            unique=False,
        )
        batch_op.create_unique_constraint(
            batch_op.f("uq_artifact_provenance_links_import_key"), ["import_key"]
        )

    with op.batch_alter_table(
        "artifact_material_requirements", schema=None
    ) as batch_op:
        batch_op.alter_column(
            "tool_index",
            existing_type=sa.INTEGER(),
            server_default=sa.text("'0'"),
            existing_nullable=False,
        )

    # ### end Alembic commands ###
