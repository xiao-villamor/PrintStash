"""Fixtures for the backend E2E layer: the real app + contract-enforcing fakes.

Unlike the unit suite (which mocks ``get_http_client`` per test), the E2E layer
drives the **real** FastAPI app and lets its real outbound HTTP stack reach a
fake provider server over a real loopback socket. That is what catches payload
bugs the unit tests can't see — a renderer that builds a request a real provider
would reject.

Design notes:
- The app runs in-process via ``httpx.ASGITransport`` (the real app, real routers,
  real services). Background work runs on the root conftest's ``InlineJobEngine``:
  a test calls ``tests.e2e._jobs.settle()`` to drain every Job the flow queued
  (and every Job those nudged), running the shipped definitions deterministically. The
  engine port itself is held to the same contract on DBOS in
  ``tests/contract/modules/work/test_contracts.py``, and ``test_job_engine.py``
  drives real DBOS.
- The DB is a private on-disk SQLite engine with production connection pragmas;
  ``data_dir`` and friends are redirected to a tmp dir through the ``_overlay``
  (every ``Settings`` field is overlay-resolvable).
- ``is_public_url`` is relaxed for loopback — real targets are public, the fake
  is on 127.0.0.1. This is the only monkeypatch; everything else is configuration.
"""

from __future__ import annotations

import shutil
import socket
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator
from urllib.request import urlopen

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import event
from sqlmodel import Session, SQLModel, create_engine

import app.modules.storage.storage_backend.runtime as storage_runtime
import app.runtime.maintenance as backup_maintenance
from app.core import url_safety
from app.core.config import _overlay
from app.db.session import (
    SQLiteSessionFactory,
    _set_sqlite_pragmas,
    override_session_factory,
)
from app.modules.notifications import notification_renderers as renderers
from app.modules.storage.storage_backend.local import LocalStorageBackend
from app.modules.storage.storage_backend.runtime import bind_backend
from tests._env import use_local_storage
from tests.fakes.provider_targets import build_provider_app
from tests.fakes.recorder import Recorder
from tests.fakes.server import RunningServer, start_server


@dataclass
class Fakes:
    """Handles for the running fake external services."""

    recorder: Recorder
    base_url: str  # e.g. http://127.0.0.1:54321

    @property
    def discord_url(self) -> str:
        return f"{self.base_url}/discord/webhook/123/abc"

    @property
    def ntfy_server(self) -> str:
        # render_ntfy posts to ``{server}/{topic}``; namespace under /ntfy.
        return f"{self.base_url}/ntfy"

    @property
    def webhook_url(self) -> str:
        return f"{self.base_url}/webhook"

    def flaky_webhook_url(self, key: str) -> str:
        return f"{self.base_url}/flaky/{key}"


@pytest.fixture
def e2e_db(tmp_path: Path) -> Iterator[Session]:
    """A real on-disk SQLite DB for the E2E layer.

    On-disk (not the unit suite's shared in-memory connection) so the app's
    request handlers and the dispatcher's worker threads each get their own
    connection — exactly like production — instead of contending over one
    StaticPool connection. The session factory is overridden so the live app and
    the test read/write the same database.
    """
    db_file = tmp_path / "e2e.sqlite"
    engine = create_engine(
        f"sqlite:///{db_file}", connect_args={"check_same_thread": False}
    )
    # Configure the first connection before schema creation or concurrent reads.
    # Switching DELETE journals to WAL after work starts can fail immediately.
    event.listen(engine, "connect", _set_sqlite_pragmas)
    SQLModel.metadata.create_all(engine)
    override_session_factory(SQLiteSessionFactory(engine))
    _overlay["db_url"] = f"sqlite:///{db_file}"
    # Keep credential encryption stable when setup-driven tests apply the
    # persisted provider overlay. This is equivalent to a per-worker
    # VAULT_SECRETS_KEY and prevents one E2E case's generated key from leaking
    # into another case.
    _overlay["secrets_key"] = "printstash-e2e-secrets-key"
    _overlay["setup_mode"] = "trusted_network"
    _overlay["setup_allowed_hosts"] = "app"
    # Redirect every storage/data dir into the test's tmp dir so nothing touches
    # the real /data tree (overlay wins over the frozen Settings defaults).
    dir_keys = ("data_dir", "thumb_dir", "staging_dir", "backup_dir")
    for key in dir_keys:
        d = tmp_path / key
        d.mkdir(parents=True, exist_ok=True)
        _overlay[key] = d
    backup_dir = Path(_overlay["backup_dir"])
    # The parent autouse fixture binds storage before this E2E fixture applies
    # its per-test directories. Rebind the adapter so worker threads use the
    # same (still fresh/un-enrolled) roots as the ASGI app. Direct-storage E2E
    # flows enroll these roots in ``superuser_headers`` below; setup-driven
    # flows must see genuinely empty roots here.
    bind_backend(LocalStorageBackend())
    session = Session(engine)
    try:
        yield session
    finally:
        # A failed restore may leave the process gate set while its durable
        # journal remains under this test's private backup directory. Clear
        # only test-owned evidence after all clients have stopped, so the next
        # E2E case cannot inherit maintenance state or a bound backend.
        backup_maintenance.end_restore_maintenance()
        for journal in backup_dir.glob(".restore-*.journal"):
            journal.unlink(missing_ok=True)
        storage_runtime._backend = None
        session.close()
        engine.dispose()
        for key in dir_keys:
            _overlay.pop(key, None)
        _overlay.pop("secrets_key", None)


@pytest.fixture
def fakes(monkeypatch: pytest.MonkeyPatch) -> Iterator[Fakes]:
    """Start the fake provider server and wire the app's egress to it."""
    recorder = Recorder()
    server: RunningServer = start_server(build_provider_app(recorder))

    # Point the Telegram renderer at the fake Bot API (host is otherwise fixed).
    monkeypatch.setattr(renderers, "TELEGRAM_API_BASE", server.base_url)
    # Allow loopback targets — real providers are public, the fake is on 127.0.0.1.
    # Only the IP classification is relaxed: resolution and the pinned transport
    # still run for real, so the dispatcher's egress path is the shipped one.
    monkeypatch.setattr(url_safety, "is_public_ip", lambda _ip: True)

    try:
        yield Fakes(recorder=recorder, base_url=server.base_url)
    finally:
        server.stop()


@pytest_asyncio.fixture
async def api(e2e_db: Session) -> "httpx.AsyncClient":
    """An async client bound to the real app via ASGI (no network for ingress).

    The process-wide outbound httpx client is reset around each test: it caches a
    client bound to the first test's event loop, which is closed by the time the
    next async test runs, so without this the dispatcher's real egress raises
    "Event loop is closed".
    """
    from app.core.http_client import close_http_client
    from app.db.session import get_session_factory
    from app.main import app
    from app.modules.printing.printer_hub import PrinterHub
    from app.modules.printing.printer_provider import (
        build_provider_registry,
        get_provider_client,
    )
    from app.runtime.realtime import InProcessBus

    # E2E must initialize its own process-local runtime state instead of
    # depending on an earlier unit test's app fixture having populated it.
    registry = build_provider_registry()
    app.state.printer_provider_registry = registry
    bus = InProcessBus()
    app.state.event_bus = bus
    app.state.printer_hub = PrinterHub(
        bus,
        session_factory=get_session_factory(),
        provider_builder=lambda printer: get_provider_client(
            printer, registry=registry
        ),
    )

    await close_http_client()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://app") as client:
        client.headers["Origin"] = "http://app"
        status = await client.get("/api/v1/setup/status")
        if not status.json()["configured"]:
            preparation = await client.post("/api/v1/setup/session")
            assert preparation.status_code == 200, preparation.text
            client.headers["X-PrintStash-Setup-CSRF"] = preparation.json()["csrf"]
        yield client
    await close_http_client()


@pytest.fixture
def superuser_headers(e2e_db, tmp_path: Path) -> dict[str, str]:
    """Seed a superuser and return its bearer header (for admin-only endpoints)."""
    from app.db.models import User
    from app.modules.identity.auth import create_access_token, hash_password

    user = User(
        username="e2e-admin",
        hashed_password=hash_password("Password123"),
        is_active=True,
        is_superuser=True,
    )
    e2e_db.add(user)
    e2e_db.commit()
    e2e_db.refresh(user)
    use_local_storage(tmp_path)
    bind_backend(LocalStorageBackend())
    token = create_access_token(user.id, user.username, scope="admin")
    return {"Authorization": f"Bearer {token}"}


def _free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


@pytest.fixture
def webdav_endpoint(tmp_path: Path):
    executable = shutil.which("wsgidav")
    if executable is None:
        pytest.fail("WsgiDAV E2E dependency is not installed; install the dev extra")
    port = _free_port()
    remote_root = tmp_path / "webdav"
    remote_root.mkdir()
    process = subprocess.Popen(
        [
            executable,
            "--host=127.0.0.1",
            f"--port={port}",
            f"--root={remote_root}",
            "--auth=anonymous",
            "--no-config",
            "--quiet",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    endpoint = f"http://127.0.0.1:{port}"
    try:
        for _ in range(100):
            if process.poll() is not None:
                pytest.fail("WsgiDAV contract server exited during startup")
            try:
                urlopen(endpoint, timeout=0.2).close()  # noqa: S310
                break
            except Exception:
                time.sleep(0.05)
        else:
            pytest.fail("WsgiDAV contract server did not become ready")
        yield endpoint
    finally:
        process.terminate()
        process.wait(timeout=5)
