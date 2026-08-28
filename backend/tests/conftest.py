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
from tests import containers  # noqa: E402

_TIER_MARKERS = {"contract": "contract", "e2e": "e2e"}
_RESOURCE_DIRS = {"postgres": "postgres"}
# Each resource marker names the service it needs. Containers are the only path
# and their absence is an error rather than a skip — see `tests/containers.py`.
_RESOURCES = {
    "postgres": containers.POSTGRES_RESOURCE,
    "s3": containers.S3_RESOURCE,
}


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Mark the tiers that have their own lane, and refuse to run without a service.

    The tier of a test is the directory it lives in, so the marker is derived from the
    path — never from the filename. ``unit`` and ``integration`` get no marker at all:
    the ``fast`` lane selects them by path, which is exact, whereas a name-shaped
    heuristic (``*_integration.py``, ``"migration" in name``) silently mis-tiered any
    file that did not happen to match.

    ``postgres`` and ``s3`` are *resource* markers, not tiers: they gate a subset
    inside a tier and get their service from a container. If a selected test carries
    one and Docker is not running, the **session stops here** rather than skipping —
    a green run with those tests quietly absent verified none of what it claimed.
    Checking at collection means the failure arrives before any test runs, naming
    the prerequisite once instead of twenty-one times.
    """
    # Markers first, in their own pass: the `postgres` marker comes from the
    # *directory* rather than a `pytestmark`, so computing what is needed before
    # adding them would miss every postgres test.
    for item in items:
        parts = Path(str(item.path)).parts
        for directory, marker in {**_TIER_MARKERS, **_RESOURCE_DIRS}.items():
            if directory in parts:
                item.add_marker(getattr(pytest.mark, marker))

    for marker, resource in sorted(_RESOURCES.items()):
        if any(marker in item.keywords for item in items):
            containers.require_docker(resource)


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """Stop any container this session started, once."""
    containers.shutdown_containers()


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

# A handful of contract tests (contract/services/test_prusalink.py,
# test_octoprint.py, test_printer_hub.py) run the *real*
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


def _all_table_names() -> list[str]:
    """Every mapped table, so a new model cannot silently escape the wipe.

    This used to be a hand-maintained list. ``audit_logs`` was missing from it, and
    because ``AuditLog.user_id`` is a plain (non-cascading) FK, audit rows survived the
    wipe and pinned recycled user ids: the next test that hard-deleted a user got
    ``sqlite3.IntegrityError: FOREIGN KEY constraint failed``, from a row it never
    created. Deriving the list from the metadata makes that class of leak impossible.
    """
    import app.db.models  # noqa: F401 — registers every table on SQLModel.metadata

    # Insertion order, not `sorted_tables`: the `files` <-> `models` cycle makes a
    # topological sort impossible, and SQLAlchemy responds by warning and giving up
    # on the ordering as a whole rather than only on those two. So there is no
    # dependency order to delete in, which is why `_truncate_all` suspends
    # enforcement for the wipe instead of trying to order around it.
    return list(SQLModel.metadata.tables)


def _truncate_all(engine: Engine = _test_engine) -> None:
    """Wipe every table between tests, leaving foreign keys enforced afterwards.

    Constraints are suspended for the wipe and restored immediately after, which is
    the ordinary shape of a bulk teardown — the same thing `TRUNCATE ... CASCADE`
    does on PostgreSQL and Django's `flush` does everywhere. There is no dependency
    order to delete in instead: the `files` <-> `models` cycle makes a topological
    sort impossible, and one does not exist to be gotten right.

    What matters is that enforcement is live for every *test body*, and for a long
    time it was not. This helper used to issue both pragmas inside `engine.begin()`,
    and SQLite ignores `PRAGMA foreign_keys` while a transaction is open: the `OFF`
    did nothing the author intended and the `ON` restored nothing. Enforcement was
    off from the first test of the session onward, so the suite could not fail on a
    foreign-key violation at all — and `DELETE /api/v1/libraries/{id}`, which returns
    500 on a real installation, passed here. `AUTOCOMMIT` is what makes the pragmas
    take effect; `tests/repo/test_db_parity.py` is what stops this regressing again.
    """
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        conn.exec_driver_sql("PRAGMA foreign_keys=OFF")
        try:
            for table in _all_table_names():
                conn.exec_driver_sql(f"DELETE FROM {table}")
        finally:
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


def _reset_every_rate_limiter() -> None:
    """Clear every route's rate-limit window.

    A limiter built by ``rate_limit()`` is a module-level singleton holding one
    process-wide window, so a test that exhausts one leaves the next test to get
    a 429 out of nowhere — and only when the two land on the same xdist worker in
    the same order, which is the worst kind of flake to chase. The limiters are
    discovered by walking the app's own route tree rather than listed here,
    because a hand-maintained list silently misses the next route that adds one
    (which is exactly how the browser-pairing claim limiter came to leak).
    """
    from app.main import app as _app

    for limiter in _rate_limiters_in(_app):
        limiter.reset()


def _rate_limiters_in(target: object) -> Iterator[object]:
    """Every rate limiter reachable from an app or router, mounts included."""
    for route in getattr(target, "routes", ()):
        dependant = getattr(route, "dependant", None)
        if dependant is not None:
            for dep in [dependant, *dependant.dependencies]:
                limiter = getattr(dep.call, "limiter", None)
                if limiter is not None:
                    yield limiter
        # A router included with `include_router` shows up as an opaque
        # `_IncludedRouter` holding the real router; a mount holds a sub-app.
        for nested in (
            getattr(route, "original_router", None),
            getattr(route, "app", None),
        ):
            if nested is not None and nested is not target:
                yield from _rate_limiters_in(nested)


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

    _reset_every_rate_limiter()


@pytest.fixture(autouse=True)
def _isolate_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Run each test from a throwaway dir.

    A few storage tests write bare relative keys (e.g. "already.stl") straight
    through ``LocalStorageBackend``, which resolves them against cwd — without
    this they land in the repo root instead of pytest's tmp dir.
    """
    monkeypatch.chdir(tmp_path)


@pytest.fixture
def local_storage(tmp_path: Path) -> Iterator[Path]:
    """Point every storage directory at a throwaway tree for this test.

    Prefer this over calling `use_local_storage` in a test body: it tears the
    configuration down as well as setting it up. Available in every tier —
    contract and e2e tests need it too.
    """
    from tests._env import clear_local_storage, use_local_storage

    yield use_local_storage(tmp_path)
    clear_local_storage()


@pytest.fixture
def backup_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A file-based vault the backup service can read and rewrite as real files.

    In the root conftest rather than `integration/` because the unit backup tests
    need it too — that is why `unit/services/test_backup.py` was importing the
    fixture out of `integration/services/test_backup.py`, a coupling that made a
    unit test fail to collect whenever the integration file was edited.
    """
    from tests.integration._backup_harness import build_backup_env

    yield from build_backup_env(tmp_path, monkeypatch)


@pytest.fixture(autouse=True)
def _reset_factory_counters() -> None:
    """Rewind the `tests.factories` sequence counters between tests.

    The builders derive unique slugs, hashes and names from these, and the
    database is wiped per test, so rewinding them makes a generated value depend
    only on the test that asked for it. Without this, `model-1` alone becomes
    `model-97` in a full run, and a failure message stops being reproducible.
    """
    from tests.factories import reset_counters

    reset_counters()


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
