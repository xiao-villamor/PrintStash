"""Shared test fixtures: in-memory SQLite, FastAPI TestClient, DB session."""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool, StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

# Settings() (app.core.config) reads VAULT_* env vars once at import time, so
# these must land before that import. Local dev shells export their own
# VAULT_DATA_DIR/VAULT_DB_URL (relative, resolve fine anywhere) — setdefault
# leaves those alone. Without them (CI, a bare shell) the frozen defaults are
# absolute container paths (/data/...), which a non-root process can't create,
# breaking real-storage and real-lifespan tests. `_data/` and `*.sqlite` are
# gitignored, so this needs no cleanup.
_TEST_STORAGE_ROOT = Path(__file__).parent / "_data"
for _var, _path in (
    ("VAULT_DATA_DIR", _TEST_STORAGE_ROOT / "files"),
    ("VAULT_THUMB_DIR", _TEST_STORAGE_ROOT / "thumbs"),
    ("VAULT_STAGING_DIR", _TEST_STORAGE_ROOT / "staging"),
    ("VAULT_BACKUP_DIR", _TEST_STORAGE_ROOT / "backups"),
):
    os.environ.setdefault(_var, str(_path))
    _path.mkdir(parents=True, exist_ok=True)
_db_dir = _TEST_STORAGE_ROOT / "db"
_db_dir.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("VAULT_DB_URL", f"sqlite:///{_db_dir / 'printstash.sqlite'}")
os.environ.setdefault(
    "VAULT_SECRETS_KEY_FILE", str(_db_dir / ".printstash-secrets-key")
)

from app.core.config import _overlay, settings  # noqa: E402
from app.db.session import (  # noqa: E402
    SQLiteSessionFactory,
    _set_sqlite_pragmas,
    get_session_factory,
    override_session_factory,
)
from app.services.printer_hub import PrinterHub  # noqa: E402

# The dev shell exports a short VAULT_JWT_SECRET (e.g. "dev-jwt-secret", 14 bytes),
# which PyJWT flags with InsecureKeyLengthWarning on every token encode/decode —
# hundreds of them across the auth-heavy suite. Force a >=32-byte secret for the
# whole run at the frozen layer (the effective fallback when no overlay is set),
# so the suite runs clean regardless of the ambient value. Individual JWT tests
# still monkeypatch this per-case; they revert to this compliant baseline.
settings._frozen.jwt_secret = "printstash-test-jwt-secret-0123456789abcdef"  # 43 bytes

TEST_DATA_DIR = Path(__file__).parent / "fixtures"
TEST_DATA_DIR.mkdir(exist_ok=True)

TEST_DB_URL = "sqlite:///:memory:"
_test_engine = create_engine(
    TEST_DB_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)

# Same per-connection pragmas the app installs (notably foreign_keys=ON), so a
# delete path that violates a constraint fails here rather than in production.
event.listen(_test_engine, "connect", _set_sqlite_pragmas)

_test_factory = SQLiteSessionFactory(_test_engine)

# A handful of e2e tests (test_prusalink_integration.py, test_octoprint_
# integration.py, test_mock_printer_integration.py) run the *real*
# PrinterHub polling loop against a real mock HTTP server: it does its DB
# writes via asyncio.to_thread worker threads, genuinely concurrently with
# the test's own main-thread session reads. StaticPool hands every session
# the *same* one DBAPI connection (the only way sessions can share data on a
# bare ``:memory:`` DB), but SQLite allows only one transaction per
# connection — two truly concurrent threads fighting over it can interleave/
# abort each other's transactions, which surfaced as those tests occasionally
# reading a stale, pre-terminal job state after the terminal write had
# already committed. ``cache=shared`` lets every session open its *own* real
# connection (safe, standard concurrent SQLite) while still sharing one
# in-memory database; those three files opt into it (see their local
# ``_use_threaded_db`` autouse fixture) instead of it being the suite-wide
# default, since NullPool's real per-checkout connections add contention
# under the full suite's much higher, non-threaded concurrency.
THREADED_DB_URL = (
    "sqlite:///file:printstash_threaded_test?mode=memory&cache=shared&uri=true"
)
_threaded_engine = create_engine(
    THREADED_DB_URL,
    connect_args={"check_same_thread": False, "uri": True},
    poolclass=NullPool,
)
# Keeps the shared-cache DB alive: SQLite drops it once its last connection
# closes, and NullPool never holds one open between checkouts.
_threaded_keepalive_conn = _threaded_engine.raw_connection()
event.listen(_threaded_engine, "connect", _set_sqlite_pragmas)
_threaded_factory = SQLiteSessionFactory(_threaded_engine)


def _init_test_db(engine: Engine) -> None:
    import app.db.models  # noqa: F401 — register all tables

    SQLModel.metadata.create_all(engine)


_init_test_db(_test_engine)
_init_test_db(_threaded_engine)


_TRUNCATE_TABLES_ORDER = [
    "storage_delete_intents",
    "staging_leases",
    "owned_storage_objects",
    "vault_audit_findings",
    "vault_audit_runs",
    "inbox_items",
    "background_jobs",
    "model_stars",
    "saved_views",
    "notification_deliveries",
    "notification_channels",
    "printer_files",
    "printer_permissions",
    "printer_maintenance_logs",
    "printer_maintenance_windows",
    "print_jobs",
    "print_batches",
    "printer_material_slots",
    "printer_tools",
    "printers",
    "printer_profiles",
    "filament_profiles",
    "share_links",
    "artifact_material_requirements",
    "files",
    "model_tags",
    "tags",
    "metadata",
    "models",
    "external_libraries",
    "documents",
    "collection_permissions",
    "collections",
    "api_keys",
    "refresh_tokens",
    "users",
    "system_config",
]


def _truncate_all(engine: Engine = _test_engine) -> None:
    """Truncate all tables between tests.

    FK enforcement is off for the wipe: this is a teardown, not a delete path,
    and the listed order doesn't satisfy every constraint (metadata references
    files, which go first). Leaving it on made the DELETEs fail silently and
    leak rows into the next test.
    """
    with engine.begin() as conn:
        conn.exec_driver_sql("PRAGMA foreign_keys=OFF")
        for table in _TRUNCATE_TABLES_ORDER:
            try:
                conn.exec_driver_sql(f"DELETE FROM {table}")
            except Exception:
                pass
        conn.exec_driver_sql("PRAGMA foreign_keys=ON")
    # Re-create sentinel rows.
    _ensure_test_sentinels(engine)


def _ensure_test_sentinels(engine: Engine = _test_engine) -> None:
    """Create sentinel rows needed for external print job tests."""
    from app.db.models import (
        SENTINEL_FILE_HASH,
        SENTINEL_MODEL_HASH,
        File,
        FileType,
        Model,
    )

    with Session(engine) as session:
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


def _reset_test_storage() -> None:
    """Keep the shared default test roots isolated between test cases."""
    for root in (
        _TEST_STORAGE_ROOT / "files",
        _TEST_STORAGE_ROOT / "thumbs",
        _TEST_STORAGE_ROOT / "staging",
        _TEST_STORAGE_ROOT / "backups",
    ):
        root.mkdir(parents=True, exist_ok=True)
        for child in root.iterdir():
            if child.is_dir() and not child.is_symlink():
                shutil.rmtree(child)
            else:
                child.unlink(missing_ok=True)


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
    _overlay["secrets_key"] = "printstash-test-secrets-key"
    _reset_test_storage()
    _truncate_all()
    # Drop the process-wide httpx client so a test that drives async egress in
    # its own asyncio.run() loop doesn't inherit one bound to a prior (closed)
    # loop — the cache only rebinds on is_closed, which a closed loop doesn't
    # flip. Dropping the ref (not aclose) avoids touching the dead loop.
    import app.core.http_client as _http_client_mod

    _http_client_mod._http_client = None

    # Rate limiters are module-level singletons (one process-wide window per
    # dependency) so state leaks across tests without an explicit reset.
    from app.api.v1.auth import _login_rate_limit, _refresh_rate_limit

    _login_rate_limit.limiter.reset()  # type: ignore[attr-defined]
    _refresh_rate_limit.limiter.reset()  # type: ignore[attr-defined]


@pytest.fixture(autouse=True)
def _isolate_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Run each test from a throwaway dir.

    A few storage tests write bare relative keys (e.g. "already.stl") straight
    through ``LocalStorageBackend``, which resolves them against cwd — without
    this they land in the repo root instead of pytest's tmp dir.
    """
    monkeypatch.chdir(tmp_path)


@pytest.fixture
def db_session() -> Iterator[Session]:
    """Yield a fresh session with rollback after each test.

    Goes through the active session factory (not a hardcoded engine) so it
    tracks whatever ``threaded_hub_db`` or similar has swapped in.
    """
    session = get_session_factory().session()
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
def threaded_hub_db() -> Iterator[None]:
    """Switch the active DB to the shared-cache engine for real cross-thread access.

    For the e2e tests that run PrinterHub's actual polling loop (genuine
    asyncio.to_thread DB writes racing the test's own main-thread reads) —
    see the module docstring above ``_threaded_engine``. Runs *after*
    ``_patch_engine`` (both function-scoped autouse-before-explicit, so
    ``_patch_engine`` wins the ordering) and restores the default factory on
    teardown so later tests are unaffected.
    """
    _truncate_all(_threaded_engine)
    override_session_factory(_threaded_factory)
    try:
        yield
    finally:
        override_session_factory(_test_factory)


@pytest.fixture
def app() -> FastAPI:
    """Return the FastAPI app with in-memory DB, printer hub attached."""
    from app.main import app as _app
    from app.services.printer_hub import PrinterHub

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
def auth_headers(db_session: Session) -> dict[str, str]:
    from app.db.models import User
    from app.services.auth import create_access_token, hash_password

    user = User(
        username="test-writer",
        hashed_password=hash_password("Password123"),
        is_active=True,
        is_superuser=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    token = create_access_token(user.id, user.username, scope="admin")
    return {"Authorization": f"Bearer {token}"}
