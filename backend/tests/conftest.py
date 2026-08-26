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
_xdist_worker = os.environ.get("PYTEST_XDIST_WORKER")
_xdist_run_uid = os.environ.get("PYTEST_XDIST_TESTRUNUID")
_TEST_STORAGE_ROOT = Path(__file__).parent / "_data"
if _xdist_worker:
    # xdist workers run independent app/DB state, so their filesystem state
    # must be independent too. Include the run UID as well as the worker name:
    # two concurrent pytest sessions both have a gw0, gw1, etc.
    _xdist_namespace = f"{_xdist_run_uid or 'xdist'}-{_xdist_worker}"
    _TEST_STORAGE_ROOT /= _xdist_namespace
for _var, _path in (
    ("VAULT_DATA_DIR", _TEST_STORAGE_ROOT / "files"),
    ("VAULT_THUMB_DIR", _TEST_STORAGE_ROOT / "thumbs"),
    ("VAULT_STAGING_DIR", _TEST_STORAGE_ROOT / "staging"),
    ("VAULT_BACKUP_DIR", _TEST_STORAGE_ROOT / "backups"),
):
    if _xdist_worker:
        # The xdist controller imports this conftest first, so workers inherit
        # its serial path. Worker processes must replace that test-owned value.
        os.environ[_var] = str(_path)
    else:
        os.environ.setdefault(_var, str(_path))
    _path.mkdir(parents=True, exist_ok=True)
_db_dir = _TEST_STORAGE_ROOT / "db"
_db_dir.mkdir(parents=True, exist_ok=True)
_test_db_url = f"sqlite:///{_db_dir / 'printstash.sqlite'}"
_test_secrets_key_file = str(_db_dir / ".printstash-secrets-key")
if _xdist_worker:
    os.environ["VAULT_DB_URL"] = _test_db_url
    os.environ["VAULT_SECRETS_KEY_FILE"] = _test_secrets_key_file
else:
    os.environ.setdefault("VAULT_DB_URL", _test_db_url)
    os.environ.setdefault("VAULT_SECRETS_KEY_FILE", _test_secrets_key_file)

from app.core.config import _overlay, settings  # noqa: E402
from app.db.session import (  # noqa: E402
    SQLiteSessionFactory,
    _set_sqlite_pragmas,
    get_session_factory,
    override_session_factory,
)
from app.services.printer_hub import PrinterHub  # noqa: E402
from tests.paths import TEST_DATA_DIR  # noqa: E402


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Apply tier and resource policy from the test directory layout."""
    tests_root = Path(__file__).parent.resolve()
    postgres_available = bool(os.environ.get("PRINTSTASH_TEST_POSTGRES_URL"))
    s3_available = bool(os.environ.get("PRINTSTASH_TEST_S3_ENDPOINT"))

    for item in items:
        path = Path(str(item.path)).resolve()
        try:
            tier = path.relative_to(tests_root).parts[0]
        except (ValueError, IndexError):
            continue

        if tier == "e2e":
            item.add_marker(pytest.mark.e2e)
        elif tier == "contract":
            item.add_marker(pytest.mark.contract)

        if "postgres" in path.parts:
            item.add_marker(pytest.mark.postgres)

        if item.get_closest_marker("postgres") and not postgres_available:
            item.add_marker(
                pytest.mark.skip(
                    reason="PRINTSTASH_TEST_POSTGRES_URL is not configured"
                )
            )
        if item.get_closest_marker("s3") and not s3_available:
            item.add_marker(
                pytest.mark.skip(reason="PRINTSTASH_TEST_S3_ENDPOINT is not configured")
            )


# The dev shell exports a short VAULT_JWT_SECRET (e.g. "dev-jwt-secret", 14 bytes),
# which PyJWT flags with InsecureKeyLengthWarning on every token encode/decode —
# hundreds of them across the auth-heavy suite. Force a >=32-byte secret for the
# whole run at the frozen layer (the effective fallback when no overlay is set),
# so the suite runs clean regardless of the ambient value. Individual JWT tests
# still monkeypatch this per-case; they revert to this compliant baseline.
settings._frozen.jwt_secret = "printstash-test-jwt-secret-0123456789abcdef"  # 43 bytes

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

# A handful of contract tests under ``tests/contract/`` run the real
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
_threaded_db_name = (
    f"printstash_threaded_test_{_xdist_run_uid or 'serial'}_{_xdist_worker or 'main'}"
)
THREADED_DB_URL = (
    f"sqlite:///file:{_threaded_db_name}?mode=memory&cache=shared&uri=true"
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
    "capture_upload_slots",
    "owned_storage_objects",
    "inbox_item_results",
    "artifact_provenance_links",
    "model_source_covers",
    "model_provenance_fields",
    "provenance_captures",
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
    "model_provenance_sources",
    "model_tags",
    "tags",
    "metadata",
    "models",
    "external_libraries",
    "documents",
    "collection_permissions",
    "collections",
    "api_keys",
    "browser_devices",
    "browser_pairing_codes",
    "provider_oauth_states",
    "provider_connections",
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
    # Production binds storage during lifespan. Unit tests exercise services
    # directly, so bind the local adapter explicitly after every reset instead
    # of letting get_backend() construct infrastructure on first access.
    from app.services.storage_backend import LocalStorageBackend, bind_backend

    bind_backend(LocalStorageBackend())
    # Drop the process-wide httpx client so a test that drives async egress in
    # its own asyncio.run() loop doesn't inherit one bound to a prior (closed)
    # loop — the cache only rebinds on is_closed, which a closed loop doesn't
    # flip. Dropping the ref (not aclose) avoids touching the dead loop.
    import app.core.http_client as _http_client_mod

    _http_client_mod._http_client = None

    # Rate limiters are module-level singletons (one process-wide window per
    # dependency) so state leaks across tests without an explicit reset.
    from app.api.v1.auth import _login_rate_limit, _refresh_rate_limit
    from app.api.v1.provider_connections import _claim_limit

    _login_rate_limit.limiter.reset()  # type: ignore[attr-defined]
    _refresh_rate_limit.limiter.reset()  # type: ignore[attr-defined]
    _claim_limit.limiter.reset()  # type: ignore[attr-defined]


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
def file_backed_integration_db(_patch_engine: None, tmp_path: Path) -> Iterator[None]:
    """Use independent SQLite connections for app and background work.

    LocalTaskQueue may commit from a worker while the request and assertion
    sessions are still alive. A file-backed database with production pragmas
    gives each session its own connection without changing the application
    seam exercised by integration tests.
    """
    del _patch_engine
    db_path = tmp_path / "integration.sqlite"
    db_url = f"sqlite:///{db_path}"
    engine = create_engine(
        db_url,
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )
    event.listen(engine, "connect", _set_sqlite_pragmas)
    _init_test_db(engine)
    factory = SQLiteSessionFactory(engine)
    override_session_factory(factory)
    _overlay["db_url"] = db_url
    try:
        yield
    finally:
        engine.dispose()
        override_session_factory(_test_factory)
        _overlay["db_url"] = TEST_DB_URL


@pytest.fixture
def app() -> FastAPI:
    """Return the FastAPI app with in-memory DB, printer hub attached."""
    from app.main import app as _app
    from app.services.printer_hub import PrinterHub
    from app.services.printer_provider import (
        build_provider_registry,
        get_provider_client,
    )
    from app.services.realtime import InProcessBus
    from app.services.task_queue import LocalTaskQueue

    registry = build_provider_registry()
    _app.state.printer_provider_registry = registry
    hub = PrinterHub(
        InProcessBus(),
        session_factory=get_session_factory(),
        provider_builder=lambda printer: get_provider_client(
            printer, registry=registry
        ),
    )
    _app.state.printer_hub = hub
    _app.state.task_queue = LocalTaskQueue()
    return _app


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


@pytest.fixture
def hub() -> PrinterHub:
    from app.services.realtime import InProcessBus

    return PrinterHub(InProcessBus(), session_factory=get_session_factory())


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
