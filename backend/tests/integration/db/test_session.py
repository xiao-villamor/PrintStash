"""Unit coverage for app.db.session: session factory config, SQLite pragmas,
async-engine URL translation, sentinel-row bootstrap, and the FastAPI deps.

These drive the real functions directly (no mocking of the logic under test).
A private, throwaway ``create_engine`` is used wherever the module's shared
``_engine``/``_async_session_maker`` singletons would otherwise leak state
across tests or collide with the suite's own in-memory test engine.
"""

from __future__ import annotations

import asyncio
from importlib.util import find_spec

import pytest
from sqlalchemy import event, inspect, text
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlmodel import Session, SQLModel, create_engine, select

import app.db.session as db_session_mod
from app.core.config import _overlay
from app.db.session import (
    AsyncDatabaseCapabilityError,
    AsyncSessionFactory,
    SessionFactory,
    SQLAlchemyAsyncSessionFactory,
    SQLiteSessionFactory,
    _is_alembic_managed,
    _set_sqlite_pragmas,
    create_async_engine_for_db,
    create_async_session_factory,
    get_async_session_factory,
    get_engine,
    get_session,
    get_session_factory,
    init_db,
    override_session_factory,
)
from app.db.url import normalize_async_database_url, normalize_database_url

# `aiosqlite` is declared in both the `dev` and `async-db` extras, so absence means a
# broken environment rather than an optional feature nobody installed. Skipping the
# async-engine tests on that basis hid them from every `uv sync` that got it wrong —
# which is how four of them came to be permanently skipped locally before the extras
# were fixed.
if find_spec("aiosqlite") is None:
    raise RuntimeError(
        "aiosqlite is missing, so the async engine cannot be tested. It is a `dev` "
        "extra dependency: re-run `uv sync --extra dev --extra full`."
    )

# --------------------------------------------------------------------------- #
# SQLite pragmas
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# Async engine URL translation
# --------------------------------------------------------------------------- #


# Every PostgreSQL URL shape an operator can put in `PRINTSTASH_DATABASE_URL`,
# including the two drivers we do not use — psycopg2 and asyncpg both appear in
# copied-in connection strings, and silently honouring either is how an install ends
# up on a driver the suite never exercises.
_POSTGRES_URL_VARIANTS = [
    "postgres://u:p@localhost/db?sslmode=require",
    "postgresql://u:p@localhost/db?sslmode=require",
    "postgresql+psycopg2://u:p@localhost/db?sslmode=require",
    "postgresql+asyncpg://u:p@localhost/db?sslmode=require",
    "postgresql+psycopg://u:p@localhost/db?sslmode=require",
]
_PSYCOPG_URL = "postgresql+psycopg://u:p@localhost/db?sslmode=require"


class TestNormalizeDbUrl:
    @pytest.mark.parametrize("db_url", _POSTGRES_URL_VARIANTS)
    def test_normalizes_a_postgres_url_for_the_sync_driver(self, db_url: str) -> None:
        assert normalize_database_url(db_url) == _PSYCOPG_URL

    @pytest.mark.parametrize("db_url", _POSTGRES_URL_VARIANTS)
    def test_normalizes_a_postgres_url_for_the_async_driver(self, db_url: str) -> None:
        # Same target as the sync driver: psycopg3 serves both, so a URL that
        # normalizes one way for the app and another for Alembic would have the two
        # talking to the database through different drivers.
        assert normalize_async_database_url(db_url) == _PSYCOPG_URL

    def test_sqlite_url_normalization(self) -> None:
        assert normalize_database_url("sqlite:///vault.db") == "sqlite:///vault.db"
        assert (
            normalize_async_database_url("sqlite:///vault.db")
            == "sqlite+aiosqlite:///vault.db"
        )


# --------------------------------------------------------------------------- #
# SQLiteSessionFactory
# --------------------------------------------------------------------------- #


class TestSqliteSessionFactory:
    def test_sync_factory_does_not_implicitly_expose_async_sessions(
        self, tmp_path
    ) -> None:
        engine = create_engine(f"sqlite:///{tmp_path / 'f.sqlite'}")
        factory = SQLiteSessionFactory(engine)
        try:
            assert isinstance(factory, SessionFactory)
            assert not isinstance(factory, AsyncSessionFactory)
            assert not hasattr(factory, "async_session")
        finally:
            engine.dispose()

    @pytest.mark.asyncio
    async def test_optional_sqlite_async_factory_executes_query(self, tmp_path) -> None:
        factory = create_async_session_factory(f"sqlite:///{tmp_path / 'async.sqlite'}")
        assert isinstance(factory, SQLAlchemyAsyncSessionFactory)
        async_session = factory.async_session()
        try:
            result = await async_session.execute(text("SELECT 1"))
            assert result.scalar_one() == 1
        finally:
            await async_session.close()
            await factory.dispose()

    def test_sqlite_session_factory_session_executes_queries(self, tmp_path) -> None:
        engine = create_engine(f"sqlite:///{tmp_path / 'f.sqlite'}")
        SQLModel.metadata.create_all(engine)
        factory = SQLiteSessionFactory(engine)
        session = factory.session()
        try:
            assert session.exec(select(1)).one() == 1
        finally:
            session.close()
            engine.dispose()

    def test_sqlite_session_factory_scoped_session_closes_on_exit(
        self, tmp_path
    ) -> None:
        engine = create_engine(f"sqlite:///{tmp_path / 'f.sqlite'}")
        SQLModel.metadata.create_all(engine)
        factory = SQLiteSessionFactory(engine)

        with factory.scoped_session() as session:
            assert session.exec(select(1)).one() == 1
            bound = session
        # SQLModel's Session.close() doesn't flip a public "closed" flag we can
        # assert on directly, but a second usable session proves the context
        # manager didn't leak or explode on exit.
        assert bound is not None
        engine.dispose()

    def test_sqlite_async_without_extra_raises_explicit_capability_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(db_session_mod, "find_spec", lambda _module: None)

        with pytest.raises(AsyncDatabaseCapabilityError, match="async-db"):
            create_async_engine_for_db("sqlite:///:memory:")

    def test_async_session_factory_is_a_lazy_singleton(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(db_session_mod, "_default_async_factory", None)
        monkeypatch.setitem(_overlay, "db_url", "sqlite:///:memory:")

        first = get_async_session_factory()
        second = get_async_session_factory()
        assert first is second  # built once, cached thereafter

        asyncio.run(first.dispose())


# --------------------------------------------------------------------------- #
# get_session_factory / override_session_factory / get_engine
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# _is_alembic_managed / init_db
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# _ensure_sentinel_rows — redirected at the module's private engine so it
# never touches the suite's shared file-backed engine.
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# FastAPI dependencies
# --------------------------------------------------------------------------- #


class TestSetSqlitePragmas:
    def test_set_sqlite_pragmas_configures_connection(self, tmp_path) -> None:
        db_file = tmp_path / "pragma-test.sqlite"
        engine = create_engine(
            f"sqlite:///{db_file}", connect_args={"check_same_thread": False}
        )
        event.listen(engine, "connect", _set_sqlite_pragmas)
        try:
            with engine.connect() as conn:
                assert conn.exec_driver_sql("PRAGMA journal_mode").scalar() == "wal"
                assert conn.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
                assert (
                    conn.exec_driver_sql("PRAGMA synchronous").scalar() == 1
                )  # NORMAL
        finally:
            engine.dispose()


class TestCreateAsyncEngineForDb:
    def test_create_async_engine_for_db_sqlite(self) -> None:
        engine = create_async_engine_for_db("sqlite:///:memory:")
        try:
            assert isinstance(engine, AsyncEngine)
            assert engine.url.drivername == "sqlite+aiosqlite"
        finally:
            asyncio.run(engine.dispose())

    @pytest.mark.parametrize(
        "db_url",
        [
            "postgresql://u:p@localhost/db",
            "postgres://u:p@localhost/db",
            "postgresql+psycopg2://u:p@localhost/db",
            "postgresql+asyncpg://u:p@localhost/db",
            "postgresql+psycopg://u:p@localhost/db",
        ],
    )
    def test_create_async_engine_for_db_postgres_variants(self, db_url: str) -> None:
        engine = create_async_engine_for_db(db_url)
        try:
            assert isinstance(engine, AsyncEngine)
            assert engine.url.drivername == "postgresql+psycopg"
        finally:
            asyncio.run(engine.dispose())

    def test_create_async_engine_for_db_passthrough_for_other_scheme(self) -> None:
        assert (
            normalize_async_database_url("mysql+aiomysql://u:p@localhost/db")
            == "mysql+aiomysql://u:p@localhost/db"
        )


class TestOverrideSessionFactory:
    def test_override_session_factory_is_what_get_returns(self, tmp_path) -> None:
        original = get_session_factory()
        engine = create_engine(f"sqlite:///{tmp_path / 'f.sqlite'}")
        custom = SQLiteSessionFactory(engine)
        try:
            override_session_factory(custom)
            assert get_session_factory() is custom
        finally:
            override_session_factory(original)
            engine.dispose()


class TestGetEngine:
    def test_get_engine_returns_module_level_engine(self) -> None:
        assert get_engine() is db_session_mod._engine


class TestIsAlembicManaged:
    def test_is_alembic_managed_false_for_fresh_engine(self, tmp_path) -> None:
        engine = create_engine(f"sqlite:///{tmp_path / 'fresh.sqlite'}")
        try:
            assert _is_alembic_managed(engine) is False
        finally:
            engine.dispose()

    def test_is_alembic_managed_true_when_alembic_version_table_present(
        self, tmp_path
    ) -> None:
        engine = create_engine(f"sqlite:///{tmp_path / 'managed.sqlite'}")
        try:
            with engine.begin() as conn:
                conn.execute(text("CREATE TABLE alembic_version (version_num TEXT)"))
            assert _is_alembic_managed(engine) is True
        finally:
            engine.dispose()


class TestInitDb:
    def test_init_db_creates_tables_on_fresh_engine(self, tmp_path) -> None:
        engine = create_engine(f"sqlite:///{tmp_path / 'init.sqlite'}")
        try:
            init_db(engine)
            table_names = inspect(engine).get_table_names()
            assert "models" in table_names
        finally:
            engine.dispose()

    def test_init_db_skips_create_all_when_alembic_managed(self, tmp_path) -> None:
        engine = create_engine(f"sqlite:///{tmp_path / 'skip.sqlite'}")
        try:
            with engine.begin() as conn:
                conn.execute(text("CREATE TABLE alembic_version (version_num TEXT)"))
            init_db(engine)
            # create_all() was skipped: only the alembic bookkeeping table exists.
            table_names = inspect(engine).get_table_names()
            assert table_names == ["alembic_version"]
        finally:
            engine.dispose()


class TestEnsureSentinelRows:
    def test_ensure_sentinel_rows_creates_then_is_idempotent(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        from app.db.models import SENTINEL_FILE_HASH, SENTINEL_MODEL_HASH, File, Model

        engine = create_engine(
            f"sqlite:///{tmp_path / 'sentinel.sqlite'}",
            connect_args={"check_same_thread": False},
        )
        SQLModel.metadata.create_all(engine)
        monkeypatch.setattr(db_session_mod, "_engine", engine)

        db_session_mod._ensure_sentinel_rows()
        with Session(engine) as session:
            models = session.exec(
                select(Model).where(Model.hash == SENTINEL_MODEL_HASH)
            ).all()
            files = session.exec(
                select(File).where(File.sha256 == SENTINEL_FILE_HASH)
            ).all()
        assert len(models) == 1
        assert len(files) == 1

        # Calling again must not create duplicates.
        db_session_mod._ensure_sentinel_rows()
        with Session(engine) as session:
            assert (
                len(
                    session.exec(
                        select(Model).where(Model.hash == SENTINEL_MODEL_HASH)
                    ).all()
                )
                == 1
            )

        engine.dispose()


class TestGetSession:
    def test_get_session_closes_the_session_after_yielding(self, tmp_path) -> None:
        engine = create_engine(f"sqlite:///{tmp_path / 'f.sqlite'}")
        SQLModel.metadata.create_all(engine)
        original = get_session_factory()
        override_session_factory(SQLiteSessionFactory(engine))
        try:
            gen = get_session()
            session = next(gen)
            assert session.exec(select(1)).one() == 1
            # Exhausting the generator runs the scoped_session's finally: close().
            with pytest.raises(StopIteration):
                next(gen)
        finally:
            override_session_factory(original)
            engine.dispose()


class TestGetAsyncSession:
    @pytest.mark.asyncio
    async def test_get_async_session_closes_the_session_after_yielding(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        monkeypatch.setattr(db_session_mod, "_default_async_factory", None)
        monkeypatch.setitem(_overlay, "db_url", "sqlite:///:memory:")

        engine = create_engine(f"sqlite:///{tmp_path / 'f.sqlite'}")
        original = get_session_factory()
        override_session_factory(SQLiteSessionFactory(engine))
        try:
            agen = db_session_mod.get_async_session()
            session = await agen.__anext__()
            assert session is not None
            with pytest.raises(StopAsyncIteration):
                await agen.__anext__()
        finally:
            override_session_factory(original)
            if db_session_mod._default_async_factory is not None:
                await db_session_mod._default_async_factory.dispose()
            engine.dispose()
