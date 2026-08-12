"""Database migration runner — the single, safe entry point for bringing a
database up to the latest Alembic revision.

Used by the container entrypoint (``python -m app.db.migrate``) so migrations run
on every start, however the app is launched, instead of depending on a fragile
Compose ``command:`` line (issue #29). Idempotent: a no-op when already at head.

It also **self-heals an "orphan" database** — one whose schema was built by the
app's startup ``create_all()`` without ever recording an Alembic version (what
happened when a user removed the Compose migration step). Running ``upgrade head``
on such a DB would fail with "table already exists" because the baseline
migration tries to re-create existing tables. So if we find application tables
but no ``alembic_version``, we first verify that its schema matches the current
application metadata. Only a complete match is adopted with ``stamp head``;
partial or ambiguous schemas fail closed without writing a version marker.
"""

from __future__ import annotations

import warnings
from pathlib import Path
from typing import Any

from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import CheckConstraint, UniqueConstraint, create_engine, inspect
from sqlmodel import SQLModel

from alembic import command
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

# backend/app/db/migrate.py -> parents[2] == backend/
_BACKEND_DIR = Path(__file__).resolve().parents[2]
_SCRIPT_LOCATION = _BACKEND_DIR / "alembic"


class OrphanSchemaError(RuntimeError):
    """Raised when an unversioned database is not demonstrably current."""


def _alembic_config(url: str) -> Config:
    """Build an Alembic config programmatically (no alembic.ini file).

    Using ``Config()`` rather than ``Config("alembic.ini")`` leaves
    ``config_file_name`` as ``None``, which makes ``env.py`` skip ``fileConfig`` —
    so running migrations does not hijack the app/pytest logging configuration.
    """
    cfg = Config()
    cfg.set_main_option("script_location", str(_SCRIPT_LOCATION))
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


def _current_revision(engine) -> str | None:
    """The DB's recorded Alembic revision, or None when it has never been stamped."""
    with engine.connect() as conn:
        return MigrationContext.configure(conn).get_current_revision()


def _has_application_tables(engine) -> bool:
    """True if the DB holds tables other than Alembic's own bookkeeping table."""
    tables = set(inspect(engine).get_table_names())
    tables.discard("alembic_version")
    return bool(tables)


def _orphan_schema_issues(engine) -> list[str]:
    """Return structural differences that make ``stamp head`` unsafe.

    Adoption is intentionally conservative. Alembic compares dialect-normalized
    columns, types, nullability and defaults; explicit structural checks cover
    primary/foreign keys, unique/check constraints and partial-index predicates.
    Extra tables are tolerated so operator-owned extensions do not block startup.
    """
    import app.db.models  # noqa: F401 — register all tables on SQLModel.metadata

    inspector = inspect(engine)
    actual_tables = set(inspector.get_table_names())
    issues: list[str] = []
    with engine.connect() as connection, warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message="Cannot correctly sort tables.*",
        )
        context = MigrationContext.configure(
            connection,
            opts={"compare_type": True, "compare_server_default": True},
        )
        for difference in compare_metadata(context, SQLModel.metadata):
            issues.extend(_describe_schema_difference(difference))

    dialect = engine.dialect.name
    for table in SQLModel.metadata.tables.values():
        if table.name not in actual_tables:
            continue
        issues.extend(_constraint_issues(inspector, table))
        issues.extend(_index_issues(inspector, table, dialect))
    return sorted(set(issues))


def _describe_schema_difference(difference: Any) -> list[str]:
    """Turn Alembic autogenerate tuples into stable, operator-safe diagnostics."""
    if isinstance(difference, list):
        return [
            issue
            for nested in difference
            for issue in _describe_schema_difference(nested)
        ]
    if not isinstance(difference, tuple) or not difference:
        return ["unknown structural schema difference"]
    kind = str(difference[0])
    if kind == "remove_table":
        return []
    if kind == "add_table":
        return [f"missing table {difference[1].name}"]
    if kind in {"add_column", "remove_column"}:
        table_name = difference[2]
        column = difference[3]
        action = "missing" if kind == "add_column" else "unexpected"
        return [f"{action} column {table_name}.{column.name}"]
    if kind.startswith("modify_"):
        table_name, column_name = difference[2], difference[3]
        attribute = kind.removeprefix("modify_").replace("_", " ")
        return [f"different {attribute} for {table_name}.{column_name}"]
    if kind in {"add_index", "add_constraint"}:
        item = difference[1]
        return [f"missing {kind.removeprefix('add_')} {item.name or '<unnamed>'}"]
    if kind in {"remove_index", "remove_constraint"}:
        item = difference[1]
        return [f"unexpected {kind.removeprefix('remove_')} {item.name or '<unnamed>'}"]
    return [f"structural difference {kind}"]


def _normalized_sql(value: object | None) -> str | None:
    if value is None:
        return None
    return " ".join(str(value).lower().replace('"', "").split())


def _constraint_issues(inspector, table) -> list[str]:
    issues: list[str] = []
    expected_pk = tuple(column.name for column in table.primary_key.columns)
    actual_pk = tuple(
        inspector.get_pk_constraint(table.name).get("constrained_columns") or ()
    )
    if expected_pk != actual_pk:
        issues.append(f"different primary key for {table.name}")

    expected_unique = {
        tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    actual_unique = {
        tuple(constraint.get("column_names") or ())
        for constraint in inspector.get_unique_constraints(table.name)
    }
    if expected_unique != actual_unique:
        issues.append(f"different unique constraints for {table.name}")

    expected_fks = {
        (
            tuple(element.parent.name for element in constraint.elements),
            constraint.referred_table.name,
            tuple(element.column.name for element in constraint.elements),
            constraint.ondelete,
            constraint.onupdate,
        )
        for constraint in table.foreign_key_constraints
    }
    actual_fks = {
        (
            tuple(constraint.get("constrained_columns") or ()),
            constraint.get("referred_table"),
            tuple(constraint.get("referred_columns") or ()),
            (constraint.get("options") or {}).get("ondelete"),
            (constraint.get("options") or {}).get("onupdate"),
        )
        for constraint in inspector.get_foreign_keys(table.name)
    }
    if expected_fks != actual_fks:
        issues.append(f"different foreign keys for {table.name}")

    expected_checks = {
        _normalized_sql(constraint.sqltext)
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    }
    actual_checks = {
        _normalized_sql(constraint.get("sqltext"))
        for constraint in inspector.get_check_constraints(table.name)
    }
    if expected_checks != actual_checks:
        issues.append(f"different check constraints for {table.name}")
    return issues


def _index_issues(inspector, table, dialect: str) -> list[str]:
    actual = {index["name"]: index for index in inspector.get_indexes(table.name)}
    issues: list[str] = []
    for index in table.indexes:
        if not index.name:
            continue
        found = actual.pop(index.name, None)
        if found is None:
            issues.append(f"missing index {index.name}")
            continue
        expected_columns = tuple(index.columns.keys())
        actual_columns = tuple(found.get("column_names") or ())
        expected_where = _normalized_sql(index.dialect_options[dialect].get("where"))
        actual_where = _normalized_sql(
            (found.get("dialect_options") or {}).get(f"{dialect}_where")
        )
        if (
            expected_columns != actual_columns
            or bool(index.unique) != bool(found.get("unique"))
            or expected_where != actual_where
        ):
            issues.append(f"different index {index.name}")
    issues.extend(f"unexpected index {name}" for name in sorted(actual))
    return issues


def _create_all(url: str) -> None:
    """Build the full current schema directly from the SQLModel models."""
    import app.db.models  # noqa: F401 — register all tables on SQLModel.metadata

    engine = create_engine(url)
    try:
        SQLModel.metadata.create_all(engine)
    finally:
        engine.dispose()


def run_migrations(database_url: str | None = None) -> None:
    """Bring *database_url* (default: configured DB) to the latest revision.

    Handles all three states the entrypoint can meet:

    * **already managed** (has ``alembic_version``): applies any pending
      migrations; a no-op when already at head.
    * **orphan** (app tables but no ``alembic_version``): adopts it only when its
      structural fingerprint matches the current schema exactly; old or partial
      schemas fail closed instead of being mislabeled as current.
    * **fresh** (no tables): builds the schema directly from the models and stamps
      head, rather than replaying the historical migration chain. That chain was
      authored against SQLite and does not apply cleanly on stricter engines like
      Postgres (its baseline fails outright); on an empty database the chain's
      data backfills are no-ops, so ``create_all`` + ``stamp head`` yields an
      equivalent, head-stamped schema on every supported engine.
    """
    url = database_url or settings.db_url

    engine = create_engine(url)
    try:
        revision = _current_revision(engine)
        has_tables = _has_application_tables(engine)
    finally:
        engine.dispose()

    cfg = _alembic_config(url)

    if revision is not None:
        # Already managed by Alembic — apply any pending migrations.
        command.upgrade(cfg, "head")
    elif has_tables:
        # Orphan: only a full schema built from the current models is safe to
        # adopt at head. Any older/partial shape has an unknown baseline and
        # stamping it would silently skip the migrations it still needs.
        validation_engine = create_engine(url)
        try:
            issues = _orphan_schema_issues(validation_engine)
        finally:
            validation_engine.dispose()
        if issues:
            preview = "; ".join(issues[:8])
            if len(issues) > 8:
                preview += f"; and {len(issues) - 8} more"
            raise OrphanSchemaError(
                "unversioned database does not match the current schema; "
                f"refusing to stamp head ({preview}). Create a backup and "
                "restore the missing migration history or contact support."
            )
        logger.warning(
            "migrate: database has a complete current schema but no Alembic "
            "version — stamping head to adopt it (orphan rescue)."
        )
        command.stamp(cfg, "head")
        command.upgrade(cfg, "head")
    else:
        # Fresh database — create the schema from the models, then stamp head so
        # Alembic considers it current (engine-agnostic; avoids the SQLite-only
        # baseline migration). Future migrations apply normally from here.
        logger.info(
            "migrate: empty database — creating schema from models and stamping head"
        )
        _create_all(url)
        command.stamp(cfg, "head")

    logger.info("migrate: database is at the latest revision (head)")


def main() -> None:  # pragma: no cover - thin CLI wrapper, exercised via entrypoint
    run_migrations()


if __name__ == "__main__":  # pragma: no cover
    main()
