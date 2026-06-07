"""Shared test fixtures: in-memory SQLite, FastAPI TestClient, DB session."""

from __future__ import annotations

from pathlib import Path
from typing import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.core.config import _overlay
from app.db.session import SQLiteSessionFactory, override_session_factory
from app.services.printer_hub import PrinterHub

TEST_DATA_DIR = Path(__file__).parent / "fixtures"
TEST_DATA_DIR.mkdir(exist_ok=True)

TEST_DB_URL = "sqlite:///:memory:"
_test_engine = create_engine(
    TEST_DB_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)

_test_factory = SQLiteSessionFactory(_test_engine)


def _init_test_db() -> None:
    import app.db.models  # noqa: F401 — register all tables

    SQLModel.metadata.create_all(_test_engine)


_init_test_db()


_TRUNCATE_TABLES_ORDER = [
    "printer_files",
    "print_jobs",
    "printers",
    "filament_profiles",
    "files",
    "model_tags",
    "tags",
    "metadata",
    "models",
    "categories",
    "refresh_tokens",
    "users",
    "system_config",
]


def _truncate_all() -> None:
    """Truncate all tables between tests (respect FK order)."""
    with _test_engine.begin() as conn:
        for table in _TRUNCATE_TABLES_ORDER:
            try:
                conn.exec_driver_sql(f"DELETE FROM {table}")
            except Exception:
                pass
    # Re-create sentinel rows.
    _ensure_test_sentinels()


def _ensure_test_sentinels() -> None:
    """Create sentinel rows needed for external print job tests."""
    from app.db.models import (
        File,
        FileType,
        Model,
        SENTINEL_MODEL_HASH,
        SENTINEL_FILE_HASH,
    )

    with Session(_test_engine) as session:
        sm = session.exec(
            select(Model).where(Model.hash == SENTINEL_MODEL_HASH)
        ).first()
        if sm is None:
            sm = Model(
                name="__external__", slug="__external__", hash=SENTINEL_MODEL_HASH
            )
            session.add(sm)
            session.commit()
            session.refresh(sm)
        sf = session.exec(select(File).where(File.sha256 == SENTINEL_FILE_HASH)).first()
        if sf is None:
            sf = File(
                model_id=sm.id,
                path="/dev/null",
                original_filename="__external__",
                file_type=FileType.GCODE,
                version=1,
                size_bytes=0,
                sha256=SENTINEL_FILE_HASH,
            )
            session.add(sf)
            session.commit()


@pytest.fixture(autouse=True)
def _patch_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    """Override the session factory ContextVar to use the in-memory test engine.

    Single override point — replaces the previous double-monkeypatch of
    ``app.db.session.engine`` and ``app.services.printer_hub.engine``.
    See ADR-0001.
    """
    override_session_factory(_test_factory)
    _overlay.clear()
    _overlay["db_url"] = TEST_DB_URL
    _overlay["api_key"] = "testkey"
    _truncate_all()


@pytest.fixture
def db_session() -> Iterator[Session]:
    """Yield a fresh session with rollback after each test."""
    session = Session(_test_engine)
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture
def db_factory() -> None:
    """Override the session factory ContextVar (alias — set by _patch_engine autouse)."""
    override_session_factory(_test_factory)


@pytest.fixture
def app() -> FastAPI:
    """Return the FastAPI app with in-memory DB, printer hub attached."""
    from app.services.printer_hub import PrinterHub
    from app.main import app as _app

    hub = PrinterHub()
    _app.state.printer_hub = hub
    return _app


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


@pytest.fixture
def hub() -> PrinterHub:
    return PrinterHub()


@pytest.fixture
def auth_headers() -> dict[str, str]:
    return {"X-API-Key": "testkey"}
