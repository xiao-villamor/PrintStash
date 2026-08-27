"""Fixtures for the backend E2E layer: the real app + contract-enforcing fakes.

Unlike the unit suite (which mocks ``get_http_client`` per test), the E2E layer
drives the **real** FastAPI app and lets its real outbound HTTP stack reach a
fake provider server over a real loopback socket. That is what catches payload
bugs the unit tests can't see — a renderer that builds a request a real provider
would reject.

Design notes:
- The app runs in-process via ``httpx.ASGITransport`` (the real app, real routers,
  real services). Background polling loops are *not* started; tests invoke the
  real service entrypoints (``notifications.dispatch_due()``, hub methods, scan
  functions) directly, which is both faithful and deterministic.
- The DB is the in-memory engine from the parent ``conftest`` (shared in-process
  via ``StaticPool``); ``data_dir`` and friends are redirected to a tmp dir
  through the ``_overlay`` (every ``Settings`` field is overlay-resolvable).
- ``is_public_url`` is relaxed for loopback — real targets are public, the fake
  is on 127.0.0.1. This is the only monkeypatch; everything else is configuration.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

import httpx
import pytest
import pytest_asyncio
from sqlmodel import Session, SQLModel, create_engine

from app.core import url_safety
from app.core.config import _overlay
from app.db.session import SQLiteSessionFactory, override_session_factory
from app.services import notification_renderers as renderers
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
    SQLModel.metadata.create_all(engine)
    override_session_factory(SQLiteSessionFactory(engine))
    _overlay["db_url"] = f"sqlite:///{db_file}"
    # Redirect every storage/data dir into the test's tmp dir so nothing touches
    # the real /data tree (overlay wins over the frozen Settings defaults).
    dir_keys = ("data_dir", "thumb_dir", "staging_dir", "backup_dir")
    for key in dir_keys:
        d = tmp_path / key
        d.mkdir(parents=True, exist_ok=True)
        _overlay[key] = d
    session = Session(engine)
    try:
        yield session
    finally:
        session.close()
        engine.dispose()
        for key in dir_keys:
            _overlay.pop(key, None)


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
    from app.services.printer_hub import PrinterHub
    from app.services.printer_provider import (
        build_provider_registry,
        get_provider_client,
    )
    from app.services.realtime import InProcessBus
    from app.services.task_queue import LocalTaskQueue

    # E2E must initialize its own process-local runtime state instead of
    # depending on an earlier unit test's app fixture having populated it.
    registry = build_provider_registry()
    app.state.printer_provider_registry = registry
    app.state.printer_hub = PrinterHub(
        InProcessBus(),
        session_factory=get_session_factory(),
        provider_builder=lambda printer: get_provider_client(
            printer, registry=registry
        ),
    )
    app.state.task_queue = LocalTaskQueue()

    await close_http_client()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://app") as client:
        yield client
    await close_http_client()


@pytest.fixture
def superuser_headers(e2e_db) -> dict[str, str]:
    """Seed a superuser and return its bearer header (for admin-only endpoints)."""
    from app.db.models import User
    from app.services.auth import create_access_token, hash_password

    user = User(
        username="e2e-admin",
        hashed_password=hash_password("Password123"),
        is_active=True,
        is_superuser=True,
    )
    e2e_db.add(user)
    e2e_db.commit()
    e2e_db.refresh(user)
    token = create_access_token(user.id, user.username, scope="admin")
    return {"Authorization": f"Bearer {token}"}
