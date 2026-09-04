from __future__ import annotations

import logging
from logging.config import fileConfig

from sqlalchemy import Connection, engine_from_config, pool
from sqlmodel import SQLModel

from alembic import context
from app.core.config import settings
from app.db import models  # noqa: F401
from app.db.migration_guards import (
    acknowledged_drops,
    dropped_and_added_columns,
    refuse_possible_renames,
)
from app.db.url import normalize_database_url

logger = logging.getLogger("alembic.env")
config = context.config
default_url = "sqlite:///./dev.sqlite"
if config.get_main_option("sqlalchemy.url") == default_url:
    config.set_main_option("sqlalchemy.url", normalize_database_url(settings.db_url))

if config.config_file_name is not None:
    # disable_existing_loggers defaults to True, which would set .disabled on
    # every app.* logger not named in alembic.ini — silently muting application
    # logging for the rest of the process (and breaking caplog in any test that
    # runs a migration before asserting on app logs). Migrations configure only
    # their own logging; they must never hijack the app's. Matches the intent in
    # app/db/migrate.py, which sidesteps fileConfig entirely for the same reason.
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = SQLModel.metadata


def _process_revision_directives(context_, revision, directives) -> None:
    """Guard what autogenerate is about to write.

    Two things, both of which would otherwise land on a reviewer's judgement:

    * An **empty** migration is not written at all. `--autogenerate` with nothing to do
      produces a file whose `upgrade()` is `pass`, and a chain accumulating those makes
      every later `alembic history` harder to read for no benefit.
    * A **possible rename** stops the run. See `_refuse_possible_renames`.
    """
    script = directives[0]
    if script.upgrade_ops.is_empty():
        directives[:] = []
        logger.info("migrate: models and database already agree — no migration written")
        return
    acknowledged = {
        entry.strip()
        for entry in (
            context.get_x_argument(as_dictionary=True).get("allow_column_drop") or ""
        ).split(",")
        if entry.strip()
    }
    changes = dropped_and_added_columns(script.upgrade_ops)
    refuse_possible_renames(changes, allowed=acknowledged)

    # Carry the acknowledgement into the file. `-x allow_column_drop=…` is otherwise
    # invisible to everyone downstream of the person who typed it.
    vetted = acknowledged_drops(changes, allowed=acknowledged)
    if vetted:
        script.message = (
            f"{script.message} "
            f"[confirmed data-dropping, not a rename: {', '.join(vetted)}]"
        )


def _render_item(type_: str, obj: object, autogen_context: object) -> str | bool:
    """Render SQLModel's own column types as plain SQLAlchemy ones.

    Autogenerate reaches for the type object it found on the model, which for a
    `str` field is `sqlmodel.sql.sqltypes.AutoString`. Two problems with letting that
    into a migration file: the generated script does not import `sqlmodel`, so it
    fails at `NameError: name 'sqlmodel' is not defined`; and a migration is a
    historical record that should not depend on the ORM layer's internals, which are
    free to move.

    `AutoString` is `sa.String` with a length, so rendering it as one loses nothing.
    """
    if type_ == "type" and type(obj).__module__.startswith("sqlmodel"):
        length = getattr(obj, "length", None)
        return f"sa.String(length={length})" if length else "sa.String()"
    return False


def _is_sqlite_url(url: str) -> bool:
    return url.startswith("sqlite")


def _configure_context(
    *,
    connection: Connection | None = None,
    url: str | None = None,
) -> None:
    kwargs = {
        "target_metadata": target_metadata,
        "compare_type": True,
        "compare_server_default": True,
        "render_item": _render_item,
        "process_revision_directives": _process_revision_directives,
        "render_as_batch": (
            connection.dialect.name == "sqlite"
            if connection
            else _is_sqlite_url(url or "")
        ),
    }
    if connection is not None:
        kwargs["connection"] = connection
    else:
        kwargs["url"] = url
        kwargs["literal_binds"] = True
        kwargs["dialect_opts"] = {"paramstyle": "named"}
    context.configure(**kwargs)


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    _configure_context(url=url)

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        _configure_context(connection=connection)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
