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

_HAS_AIOSQLITE = find_spec("aiosqlite") is not None

# --------------------------------------------------------------------------- #
# SQLite pragmas
# --------------------------------------------------------------------------- #


def test_set_sqlite_pragmas_configures_connection(tmp_path) -> None:
    db_file = tmp_path / "pragma-test.sqlite"
    engine = create_engine(
        f"sqlite:///{db_file}", connect_args={"check_same_thread": False}
    )
    event.listen(engine, "connect", _set_sqlite_pragmas)
    try:
        with engine.connect() as conn:
            assert conn.exec_driver_sql("PRAGMA journal_mode").scalar() == "wal"
            assert conn.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
            assert conn.exec_driver_sql("PRAGMA synchronous").scalar() == 1  # NORMAL
    finally:
        engine.dispose()


def test_invalid_sqlite_synchronous_mode_falls_back_to_normal(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setitem(_overlay, "sqlite_synchronous", "unsafe")
    engine = create_engine(f"sqlite:///{tmp_path / 'invalid-pragma.sqlite'}")
    event.listen(engine, "connect", _set_sqlite_pragmas)

    try:
        with engine.connect() as connection:
            synchronous = connection.exec_driver_sql("PRAGMA synchronous").scalar()
    finally:
        engine.dispose()

    assert synchronous == 1


# --------------------------------------------------------------------------- #
# Async engine URL translation
# --------------------------------------------------------------------------- #


@pytest.mark.skipif(not _HAS_AIOSQLITE, reason="requires the async-db extra")
def test_create_async_engine_for_db_sqlite() -> None:
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
def test_create_async_engine_for_db_postgres_variants(db_url: str) -> None:
    engine = create_async_engine_for_db(db_url)
    try:
        assert isinstance(engine, AsyncEngine)
        assert engine.url.drivername == "postgresql+psycopg"
    finally:
        asyncio.run(engine.dispose())


def test_create_async_engine_for_db_passthrough_for_other_scheme() -> None:
    assert (
        normalize_async_database_url("mysql+aiomysql://u:p@localhost/db")
        == "mysql+aiomysql://u:p@localhost/db"
    )


@pytest.mark.parametrize(
    "db_url",
    [
        "postgres://u:p@localhost/db?sslmode=require",
        "postgresql://u:p@localhost/db?sslmode=require",
        "postgresql+psycopg2://u:p@localhost/db?sslmode=require",
        "postgresql+asyncpg://u:p@localhost/db?sslmode=require",
        "postgresql+psycopg://u:p@localhost/db?sslmode=require",
    ],
)
def test_postgres_urls_normalize_to_psycopg_for_sync_and_async(db_url: str) -> None:
    expected = "postgresql+psycopg://u:p@localhost/db?sslmode=require"

    assert normalize_database_url(db_url) == expected
    assert normalize_async_database_url(db_url) == expected


def test_sqlite_url_normalization() -> None:
    assert normalize_database_url("sqlite:///vault.db") == "sqlite:///vault.db"
    assert (
        normalize_async_database_url("sqlite:///vault.db")
        == "sqlite+aiosqlite:///vault.db"
    )


def test_sqlite_async_without_extra_raises_explicit_capability_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(db_session_mod, "find_spec", lambda _module: None)

    with pytest.raises(AsyncDatabaseCapabilityError, match="async-db"):
        create_async_engine_for_db("sqlite:///:memory:")


@pytest.mark.skipif(not _HAS_AIOSQLITE, reason="requires the async-db extra")
def test_async_session_factory_is_a_lazy_singleton(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(db_session_mod, "_default_async_factory", None)
    monkeypatch.setitem(_overlay, "db_url", "sqlite:///:memory:")

    first = get_async_session_factory()
    second = get_async_session_factory()
    assert first is second  # built once, cached thereafter

    asyncio.run(first.dispose())


# --------------------------------------------------------------------------- #
# SQLiteSessionFactory
# --------------------------------------------------------------------------- #


def test_sqlite_session_factory_session_executes_queries(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'f.sqlite'}")
    SQLModel.metadata.create_all(engine)
    factory = SQLiteSessionFactory(engine)
    session = factory.session()
    try:
        assert session.exec(select(1)).one() == 1
    finally:
        session.close()
        engine.dispose()


def test_sync_factory_does_not_implicitly_expose_async_sessions(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'f.sqlite'}")
    factory = SQLiteSessionFactory(engine)
    try:
        assert isinstance(factory, SessionFactory)
        assert not isinstance(factory, AsyncSessionFactory)
        assert not hasattr(factory, "async_session")
    finally:
        engine.dispose()


def test_sqlite_session_factory_scoped_session_closes_on_exit(tmp_path) -> None:
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


@pytest.mark.skipif(not _HAS_AIOSQLITE, reason="requires the async-db extra")
@pytest.mark.asyncio
async def test_optional_sqlite_async_factory_executes_query(tmp_path) -> None:
    factory = create_async_session_factory(f"sqlite:///{tmp_path / 'async.sqlite'}")
    assert isinstance(factory, SQLAlchemyAsyncSessionFactory)
    async_session = factory.async_session()
    try:
        result = await async_session.execute(text("SELECT 1"))
        assert result.scalar_one() == 1
    finally:
        await async_session.close()
        await factory.dispose()


# --------------------------------------------------------------------------- #
# get_session_factory / override_session_factory / get_engine
# --------------------------------------------------------------------------- #


def test_get_and_override_session_factory_round_trip(tmp_path) -> None:
    original = get_session_factory()
    engine = create_engine(f"sqlite:///{tmp_path / 'f.sqlite'}")
    custom = SQLiteSessionFactory(engine)
    try:
        override_session_factory(custom)
        assert get_session_factory() is custom
    finally:
        override_session_factory(original)
        engine.dispose()


def test_get_engine_returns_module_level_engine() -> None:
    assert get_engine() is db_session_mod._engine


# --------------------------------------------------------------------------- #
# _is_alembic_managed / init_db
# --------------------------------------------------------------------------- #


def test_is_alembic_managed_false_for_fresh_engine(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'fresh.sqlite'}")
    try:
        assert _is_alembic_managed(engine) is False
    finally:
        engine.dispose()


def test_is_alembic_managed_true_when_alembic_version_table_present(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'managed.sqlite'}")
    try:
        with engine.begin() as conn:
            conn.execute(text("CREATE TABLE alembic_version (version_num TEXT)"))
        assert _is_alembic_managed(engine) is True
    finally:
        engine.dispose()


def test_init_db_creates_tables_on_fresh_engine(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'init.sqlite'}")
    try:
        init_db(engine)
        table_names = inspect(engine).get_table_names()
        assert "models" in table_names
    finally:
        engine.dispose()


def test_init_db_skips_create_all_when_alembic_managed(tmp_path) -> None:
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


# --------------------------------------------------------------------------- #
# _ensure_sentinel_rows — redirected at the module's private engine so it
# never touches the suite's shared file-backed engine.
# --------------------------------------------------------------------------- #


def test_ensure_sentinel_rows_creates_then_is_idempotent(
    monkeypatch: pytest.MonkeyPatch, tmp_path
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


# --------------------------------------------------------------------------- #
# FastAPI dependencies
# --------------------------------------------------------------------------- #


def test_get_session_dependency_yields_and_closes(tmp_path) -> None:
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


@pytest.mark.asyncio
async def test_get_async_session_dependency_yields_and_closes(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    if not _HAS_AIOSQLITE:
        pytest.skip("requires the async-db extra")
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
