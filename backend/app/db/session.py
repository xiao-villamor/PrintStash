from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import AsyncGenerator, Generator, Iterator, Protocol, runtime_checkable

from sqlalchemy import event, inspect
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlmodel import Session, SQLModel, create_engine, select

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

# `check_same_thread=False` is required for SQLite + FastAPI (multiple threads).
_connect_args = (
    {"check_same_thread": False} if settings.db_url.startswith("sqlite") else {}
)


def _set_sqlite_pragmas(dbapi_conn, _record) -> None:
    """Per-connection SQLite tuning.

    WAL lets readers proceed while one writer commits (ingestion background
    tasks vs. browse requests); busy_timeout makes concurrent writers queue
    instead of failing immediately with 'database is locked'.
    """
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.execute("PRAGMA temp_store=MEMORY")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


_engine: Engine = create_engine(
    settings.db_url,
    echo=False,
    connect_args=_connect_args,
)

if settings.db_url.startswith("sqlite"):
    event.listen(_engine, "connect", _set_sqlite_pragmas)

_async_engine: AsyncEngine | None = None
_async_session_maker: async_sessionmaker[AsyncSession] | None = None


def create_async_engine_for_db(db_url: str) -> AsyncEngine:
    if db_url.startswith("sqlite"):
        async_url = db_url.replace("sqlite://", "sqlite+aiosqlite://", 1)
        engine = create_async_engine(
            async_url,
            echo=False,
            connect_args={"check_same_thread": False},
        )
        event.listen(engine.sync_engine, "connect", _set_sqlite_pragmas)
        return engine
    if db_url.startswith("postgresql://"):
        async_url = db_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    elif db_url.startswith("postgres://"):
        async_url = db_url.replace("postgres://", "postgresql+asyncpg://", 1)
    else:
        async_url = db_url
    return create_async_engine(async_url, echo=False, pool_pre_ping=True)


def _async_session_factory() -> async_sessionmaker[AsyncSession]:
    global _async_engine, _async_session_maker
    if _async_session_maker is None:
        _async_engine = create_async_engine_for_db(settings.db_url)
        _async_session_maker = async_sessionmaker(
            bind=_async_engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )
    return _async_session_maker


# ---------------------------------------------------------------------------
# SessionFactory Protocol & ContextVar — single seam for all session access
# See ADR-0001.
# ---------------------------------------------------------------------------


@runtime_checkable
class SessionFactory(Protocol):
    """Protocol for session factories — the single seam for DB session access.

    Two lifecycle patterns:
    - ``session()`` returns a raw Session — caller owns commit/close (background tasks).
    - ``scoped_session()`` returns a context manager — auto-closes on exit (FastAPI deps, ingestion).

    ``async_session()`` is a placeholder for Stage 4 Postgres.
    """

    def session(self) -> Session: ...
    def async_session(self) -> AsyncSession: ...
    def scoped_session(self) -> Generator[Session, None, None]: ...
    def dispose(self) -> None: ...


class SQLiteSessionFactory:
    """Default production adapter: SQLModel sessions from the module-level engine."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def session(self) -> Session:
        return Session(self._engine)

    def async_session(self) -> AsyncSession:
        maker = _async_session_factory()
        return maker()

    def dispose(self) -> None:
        """Close idle pooled connections before/after a database restore."""
        self._engine.dispose()

    @contextmanager
    def scoped_session(self) -> Generator[Session, None, None]:
        session = Session(self._engine)
        try:
            yield session
        finally:
            session.close()


_factory_ctx: ContextVar[SessionFactory] = ContextVar(
    "session_factory",
    default=SQLiteSessionFactory(_engine),  # noqa: B039
)


def get_session_factory() -> SessionFactory:
    """Return the active SessionFactory from the context.

    Used by FastAPI dependencies and callers that need to create sessions
    without coupling to the module-level engine.  Tests override via
    ``override_session_factory()``, not monkeypatching.
    """
    return _factory_ctx.get()


def override_session_factory(factory: SessionFactory) -> None:
    """Override the ContextVar for testing. Restore after test teardown."""
    _factory_ctx.set(factory)


def get_engine() -> Engine:
    """Return the module-level engine. Only for low-level operations (backup restore, disposal)."""
    return _engine


def _is_alembic_managed(engine: Engine) -> bool:
    """True when the DB's schema is owned by Alembic (an ``alembic_version`` table
    exists) — i.e. migrations have run against it.

    In that case ``create_all()`` must NOT also build tables: it can't reproduce
    the data backfills/ALTERs the migrations carry, and on a fresh DB it would
    leave an un-stamped, divergent schema. See ``app/db/migrate.py``.
    """
    try:
        return "alembic_version" in inspect(engine).get_table_names()
    except Exception:  # pragma: no cover - defensive; treat unreadable as unmanaged
        return False


def init_db(engine: Engine | None = None) -> None:
    """Bootstrap a database that Alembic has not already built.

    Production runs migrations *before* the app starts (see ``app/db/migrate.py``
    and the container entrypoint), so the schema is Alembic-owned and this is a
    no-op. The direct ``create_all()`` path remains only for the test suite and a
    brand-new local dev database that hasn't been migrated yet — never on top of
    an Alembic-managed database, which is what used to produce a divergent,
    un-stamped schema (issue #29).
    """
    eng = engine if engine is not None else _engine
    from app.db import models  # noqa: F401

    if _is_alembic_managed(eng):
        return
    SQLModel.metadata.create_all(eng)


def _ensure_sentinel_rows() -> None:
    """Create sentinel Model + File rows used by external (non-vault) print jobs."""
    from app.db.models import (
        SENTINEL_FILE_HASH,
        SENTINEL_MODEL_HASH,
        File,
        FileType,
        Model,
    )

    with Session(_engine) as session:
        sentinel_model = session.exec(
            select(Model).where(Model.hash == SENTINEL_MODEL_HASH)
        ).first()
        if sentinel_model is None:
            sentinel_model = Model(
                name="__external__",
                slug="__external__",
                hash=SENTINEL_MODEL_HASH,
            )
            session.add(sentinel_model)
            session.commit()
            session.refresh(sentinel_model)

        sentinel_file = session.exec(
            select(File).where(File.sha256 == SENTINEL_FILE_HASH)
        ).first()
        if sentinel_file is None:
            sentinel_file = File(
                model_id=sentinel_model.id,
                path="/dev/null",
                original_filename="__external__",
                file_type=FileType.GCODE,
                version=1,
                size_bytes=0,
                sha256=SENTINEL_FILE_HASH,
            )
            session.add(sentinel_file)
            session.commit()


def get_session() -> Iterator[Session]:
    """FastAPI dependency: yields a scoped Session and ensures cleanup."""
    factory = _factory_ctx.get()
    with factory.scoped_session() as session:
        yield session


async def get_async_session() -> AsyncGenerator[AsyncSession, None]:
    factory = _factory_ctx.get()
    session = factory.async_session()
    try:
        yield session
    finally:
        await session.close()
