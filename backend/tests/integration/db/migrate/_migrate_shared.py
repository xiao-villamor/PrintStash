"""Exercise Alembic upgrades against concrete SQLite schemas and revisions.

Failures signal an upgrade path that can strand existing installations.
"""

from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, SQLModel

import app.db.models  # noqa: F401 — register all tables on SQLModel.metadata
from alembic import command
from app.db import migrate as migrate_mod
from app.db.models import User
from app.db.session import _is_alembic_managed, init_db
from tests.paths import BACKEND_ROOT


# --------------------------------------------------------------------------- #
# Strict coverage for the migration runner (app/db/migrate.py) and create_all
# gating — the entrypoint hardening for issue #29. Runs the real migration chain
# against temp SQLite *files* in every DB state the entrypoint must survive.
# --------------------------------------------------------------------------- #
def _url(tmp_path: Path, name: str = "runner.sqlite") -> str:
    return f"sqlite:///{tmp_path / name}"


def _head_revision() -> str:
    cfg = migrate_mod._alembic_config("sqlite://")
    head = ScriptDirectory.from_config(cfg).get_current_head()
    assert head is not None
    return head


def _current(url: str) -> str | None:
    engine = create_engine(url)
    try:
        return migrate_mod._current_revision(engine)
    finally:
        engine.dispose()


def _table_names(url: str) -> set[str]:
    engine = create_engine(url)
    try:
        return set(inspect(engine).get_table_names())
    finally:
        engine.dispose()


def _rewrite_sqlite_table_definition(
    url: str,
    table_name: str,
    old: str,
    new: str,
) -> None:
    engine = create_engine(url)
    try:
        with engine.begin() as connection:
            statement = connection.execute(
                text(
                    "SELECT sql FROM sqlite_master "
                    "WHERE type = 'table' AND name = :name"
                ),
                {"name": table_name},
            ).scalar_one()
            assert old in statement
            connection.exec_driver_sql("PRAGMA writable_schema=ON")
            connection.execute(
                text(
                    "UPDATE sqlite_master SET sql = :sql "
                    "WHERE type = 'table' AND name = :name"
                ),
                {"sql": statement.replace(old, new), "name": table_name},
            )
            schema_version = connection.exec_driver_sql(
                "PRAGMA schema_version"
            ).scalar_one()
            connection.exec_driver_sql(f"PRAGMA schema_version={schema_version + 1}")
            connection.exec_driver_sql("PRAGMA writable_schema=OFF")
    finally:
        engine.dispose()


# --------------------------------------------------------------------------- #
# State dispatch: a fresh DB must NOT replay the historical migration chain
# (its baseline is SQLite-only and fails on Postgres) — it bootstraps via
# create_all + stamp instead. This is what makes Postgres work; asserted here
# without needing a Postgres service in CI.
# --------------------------------------------------------------------------- #
class _Spy:
    def __init__(self) -> None:
        self.upgrade: list = []
        self.stamp: list = []
        self.create_all: list = []

    def install(self, monkeypatch, tmp_path: Path) -> str:
        url = _url(tmp_path, "dispatch.sqlite")
        monkeypatch.setattr(
            migrate_mod.command, "upgrade", lambda *a, **k: self.upgrade.append(a)
        )
        monkeypatch.setattr(
            migrate_mod.command, "stamp", lambda *a, **k: self.stamp.append(a)
        )
        monkeypatch.setattr(
            migrate_mod, "_create_all", lambda u: self.create_all.append(u)
        )
        return url


# --------------------------------------------------------------------------- #
# Upgrade-from-an-old-release guards. A self-hoster on an older version runs
# `upgrade head` at container start; if the chain has branched (two heads) or a
# revision file was deleted/renamed (down_revision can't resolve), that crashes
# the api container and takes the whole stack down. These catch both in CI.
# --------------------------------------------------------------------------- #

# Last released migration before the 0.8.0 line (present in the 0.7.2 tree) — a
# realistic point an existing install is upgrading *from*.
_PRE_0_8_0 = "f7a5b3c9d2e1"


# ---------------------------------------------------------------------------
# Orphan-row repair before foreign key enforcement (b2d8f6a1c94e)
# ---------------------------------------------------------------------------


def _upgrade_to(db_path: Path, revision: str) -> Config:
    cfg = Config(str(BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    command.upgrade(cfg, revision)
    return cfg


# ---------------------------------------------------------------------------
# print_jobs.cost backfill (175be54ef975)
# ---------------------------------------------------------------------------

__all__ = [
    "BACKEND_ROOT",
    "Config",
    "IntegrityError",
    "Path",
    "SQLModel",
    "ScriptDirectory",
    "Session",
    "User",
    "_PRE_0_8_0",
    "_Spy",
    "_current",
    "_head_revision",
    "_is_alembic_managed",
    "_rewrite_sqlite_table_definition",
    "_table_names",
    "_upgrade_to",
    "_url",
    "command",
    "create_engine",
    "init_db",
    "inspect",
    "migrate_mod",
    "module_from_spec",
    "pytest",
    "spec_from_file_location",
    "text",
]
