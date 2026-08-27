"""Boots the real app lifespan (startup + shutdown), not just handler-level tests.

Every other test in the suite gets its FastAPI ``app`` fixture pre-wired
(``app.state.printer_hub`` set manually, no ``with TestClient(app) as client``),
so ``app/main.py``'s ``lifespan()`` — DB init, storage init, background task
wiring, graceful shutdown — had no direct coverage (58% per the 0.11 audit).
This starts it for real via Starlette's TestClient context-manager protocol.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from io import BytesIO
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlmodel import select
from starlette.requests import Request as StarletteRequest

import app.main as app_main
from app.core.config import _overlay
from app.db.models import (
    CaptureUploadSlot,
    CaptureUploadSlotState,
    InboxItem,
    InboxItemState,
    InboxSourceKind,
    Model,
    ModelProvenanceSource,
    OwnedStorageObject,
    Printer,
    PrinterProvider,
    PrinterStatus,
    StagingLease,
    User,
)
from app.services import storage_backend
from app.services.auth import create_access_token, hash_password
from app.services.realtime import InProcessBus
from app.services.storage_backend import ObjectIdentity, StorageCapabilities


@pytest.fixture
def _local_storage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _overlay.update(
        {
            "storage_backend": "local",
            "data_dir": tmp_path / "files",
            "thumb_dir": tmp_path / "thumbs",
            "backup_dir": tmp_path / "backups",
            "staging_dir": tmp_path / "staging",
        }
    )
    monkeypatch.setattr(storage_backend, "_backend", None)
    yield
    for field in (
        "storage_backend",
        "data_dir",
        "thumb_dir",
        "backup_dir",
        "staging_dir",
    ):
        _overlay.pop(field, None)


def test_lifespan_starts_background_tasks_and_shuts_down_cleanly(
    _local_storage: None, db_session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.main import app

    user = User(
        username="lifespan-admin",
        hashed_password=hash_password("Password123"),
        is_active=True,
        is_superuser=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    token = create_access_token(user.id, user.username, scope="admin")
    headers = {"Authorization": f"Bearer {token}"}

    with TestClient(app) as client:
        # Background tasks wired onto app.state by the real lifespan, not the
        # per-test fixture shortcut.
        for attr in (
            "printer_hub",
            "library_watcher",
            "gc_task",
            "external_scan_task",
            "notification_task",
            "fleet_scheduler_task",
            "task_queue",
        ):
            assert hasattr(app.state, attr), f"app.state.{attr} not set by lifespan"
        assert isinstance(app.state.printer_hub.bus, InProcessBus)
        assert app.state.printer_hub._session_factory is app_main.get_session_factory()
        assert not app.state.fleet_scheduler_task.done()
        assert not app.state.gc_task.done()

        response = client.get("/api/v1/health/details", headers=headers)
        assert response.status_code == 200
        body = response.json()
        # Not asserting overall body["status"] == "ok": components like backup
        # (none configured) legitimately report degraded on a fresh install —
        # this test is about the scheduler/storage wiring lifespan sets up,
        # not full green health.
        assert body["components"]["fleet_scheduler"]["ok"] is True
        assert body["components"]["fleet_scheduler"]["running"] is True
        assert body["components"]["storage"]["ok"] is True

        liveness = client.get("/api/v1/health")
        assert liveness.status_code == 200
        public_storage = liveness.json()["storage"]
        assert public_storage["tier"] == "verified"
        assert len(public_storage["diagnostics"]["roots"]) == 2

    # Shutdown (TestClient.__exit__) must cancel every background task.
    assert app.state.fleet_scheduler_task.cancelled()
    assert app.state.gc_task.cancelled()
    assert app.state.external_scan_task.cancelled()
    assert app.state.notification_task.cancelled()


def test_safe_db_url_returns_placeholder_for_unparseable_url() -> None:
    # make_url() raises on garbage input; the helper must degrade instead of
    # crashing the startup log line.
    assert app_main._safe_db_url("not a valid :// url") == "<invalid-db-url>"
    assert app_main._safe_db_url("sqlite:///tmp/x.db").endswith("x.db")


def test_storage_composition_runs_publication_recovery_after_binding(
    _local_storage: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services import inbox

    events: list[str] = []

    class _Backend:
        backend_name = "test"
        capabilities = StorageCapabilities(
            conditional_create=True,
            object_identity=ObjectIdentity.INODE,
            verified_delete=True,
            conditional_replace=True,
            namespace_ownership=True,
            direct_path=True,
        )
        probe_diagnostics: dict[str, object] = {}

        def ensure_setup(self) -> None:
            events.append("ensure")

    backend = _Backend()
    monkeypatch.setattr(app_main, "LocalStorageBackend", lambda: backend)
    monkeypatch.setattr(
        app_main,
        "bind_backend",
        lambda value: events.append("bind") or value,
    )
    monkeypatch.setattr(
        inbox,
        "reconcile_storage_publications",
        lambda: events.append("recover") or 1,
    )

    assert app_main._compose_storage_backend() is backend
    assert events == ["ensure", "bind", "recover"]


def test_storage_composition_rejects_unacknowledged_unguarded_backend(
    _local_storage: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend = storage_backend.LocalStorageBackend()
    backend._capabilities = StorageCapabilities(  # noqa: SLF001
        conditional_create=False,
        object_identity=ObjectIdentity.NONE,
        verified_delete=False,
        conditional_replace=False,
        namespace_ownership=True,
        direct_path=False,
    )
    monkeypatch.setattr(backend, "ensure_setup", lambda: None)
    monkeypatch.setattr(app_main, "LocalStorageBackend", lambda: backend)

    with pytest.raises(RuntimeError, match="VAULT_STORAGE_ALLOW_UNVERIFIED=true"):
        app_main._compose_storage_backend()


def test_storage_composition_accepts_acknowledged_unguarded_backend(
    _local_storage: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services import inbox

    backend = storage_backend.LocalStorageBackend()
    backend._capabilities = StorageCapabilities(  # noqa: SLF001
        conditional_create=False,
        object_identity=ObjectIdentity.NONE,
        verified_delete=False,
        conditional_replace=False,
        namespace_ownership=True,
        direct_path=False,
    )
    monkeypatch.setattr(backend, "ensure_setup", lambda: None)
    monkeypatch.setattr(app_main, "LocalStorageBackend", lambda: backend)
    monkeypatch.setattr(app_main, "bind_backend", lambda value: value)
    monkeypatch.setattr(inbox, "reconcile_storage_publications", lambda: 0)
    monkeypatch.setitem(_overlay, "storage_allow_unverified", True)

    assert app_main._compose_storage_backend() is backend


def test_storage_composition_warns_without_blocking_guarded_backend(
    _local_storage: None,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from app.services import inbox

    backend = storage_backend.LocalStorageBackend()
    backend._capabilities = StorageCapabilities(  # noqa: SLF001
        conditional_create=True,
        object_identity=ObjectIdentity.ETAG,
        verified_delete=False,
        conditional_replace=False,
        namespace_ownership=True,
        direct_path=False,
    )
    monkeypatch.setattr(backend, "ensure_setup", lambda: None)
    monkeypatch.setattr(app_main, "LocalStorageBackend", lambda: backend)
    monkeypatch.setattr(app_main, "bind_backend", lambda value: value)
    monkeypatch.setattr(inbox, "reconcile_storage_publications", lambda: 0)

    with caplog.at_level("WARNING"):
        assert app_main._compose_storage_backend() is backend

    assert "storage capability warning" in caplog.text
    assert "cannot conditionally replace" in caplog.text


def test_storage_composition_recovers_cover_published_before_restart_binding(
    _local_storage: None, db_session
) -> None:
    from app.services import source_covers, storage_backend
    from app.services.storage_backend import LocalStorageBackend, get_backend

    model = Model(
        name="Startup recovery model",
        slug="startup-recovery-model",
        hash="a" * 64,
    )
    db_session.add(model)
    db_session.flush()
    source = ModelProvenanceSource(
        model_id=model.id,
        provider="test",
        canonical_url="https://example.test/startup-recovery",
        identity_key="startup-recovery",
    )
    db_session.add(source)
    db_session.commit()

    # The fixture has a local backend bound for service setup. Simulate a
    # process crash after publication and before the caller's receipt commit.
    storage_backend.bind_backend(LocalStorageBackend())
    image = BytesIO()
    Image.new("RGB", (1, 1), "navy").save(image, format="PNG")
    result = source_covers.put(
        db_session,
        get_backend(),
        provenance_source_id=source.id,
        actor_id=None,
        data=image.getvalue(),
        content_type="image/png",
    )
    db_session.rollback()
    assert db_session.exec(select(StagingLease)).all()

    storage_backend._backend = None
    app_main._compose_storage_backend()
    assert db_session.exec(select(StagingLease)).all() == []
    assert (
        db_session.exec(select(OwnedStorageObject)).one().key
        == result.cover.storage_key
    )


def test_startup_reconciles_completed_capture_slot_after_storage_binding(
    _local_storage: None, db_session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A completed browser capture must recover its publication at startup.

    This is deliberately a bounded startup seam test instead of a TestClient
    lifespan portal: the ordering under test is synchronous and should not
    depend on background-task shutdown completing.
    """
    from app.db.session import get_session_factory
    from app.services import inbox, staging_leases, storage_backend
    from app.services.storage_backend import LocalStorageBackend

    owner = User(username="startup-capture-owner", hashed_password="hash")
    db_session.add(owner)
    db_session.flush()
    assert owner.id is not None
    item = InboxItem(
        owner_user_id=owner.id,
        source_kind=InboxSourceKind.BROWSER,
        state=InboxItemState.COMPLETED,
    )
    db_session.add(item)
    db_session.flush()
    assert item.id is not None

    payload = b"completed capture slot publication"
    slot_id = "startup-completed-slot"
    unbound_backend = LocalStorageBackend()
    storage_key = unbound_backend.capture_upload_slot_key(slot_id)
    slot = CaptureUploadSlot(
        id=slot_id,
        inbox_item_id=item.id,
        role="file",
        filename="capture.stl",
        media_type="model/stl",
        size_bytes=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
        state=CaptureUploadSlotState.PENDING,
        storage_key=storage_key,
    )
    db_session.add(slot)
    db_session.flush()
    staging_leases.create_capture_slot_lease(
        db_session,
        slot_id=slot.id,
        owner_user_id=owner.id,
        destination_key=storage_key,
        size_bytes=slot.size_bytes,
        sha256=slot.sha256,
    )
    db_session.commit()
    Path(storage_key).parent.mkdir(parents=True, exist_ok=True)
    Path(storage_key).write_bytes(payload)

    events: list[str] = []

    class _TrackingBackend(LocalStorageBackend):
        def ensure_setup(self) -> None:
            events.append("ensure")
            super().ensure_setup()

        def adopt_existing(self, key: str, *, expected_size: int, expected_sha256: str):
            events.append("recover")
            return super().adopt_existing(
                key, expected_size=expected_size, expected_sha256=expected_sha256
            )

    backend = _TrackingBackend()
    monkeypatch.setattr(app_main, "LocalStorageBackend", lambda: backend)
    real_bind = storage_backend.bind_backend

    def record_bind(value):
        events.append("bind")
        return real_bind(value)

    monkeypatch.setattr(app_main, "bind_backend", record_bind)

    def record_interrupted_reconcile() -> int:
        events.append("inbox")
        assert storage_backend.get_backend() is backend
        with get_session_factory().scoped_session() as session:
            recovered = session.get(CaptureUploadSlot, slot_id)
            assert recovered is not None
            assert recovered.state == CaptureUploadSlotState.UPLOADED
            assert recovered.receipt_json
        return 0

    monkeypatch.setattr(
        inbox, "reconcile_interrupted_items", record_interrupted_reconcile
    )
    storage_backend._backend = None

    assert app_main._prepare_storage_for_startup() is backend
    assert events == ["ensure", "bind", "recover", "inbox"]


def test_parse_cors_origins_accepts_list_and_filters_blanks() -> None:
    assert app_main._parse_cors_origins(
        ["http://a.example", "  ", "http://b.example"]
    ) == ["http://a.example", "http://b.example"]
    assert app_main._parse_cors_origins(42) == []


@pytest.mark.asyncio
async def test_cancel_tasks_awaits_cleanup_and_consumes_cancellation() -> None:
    cleaned_up = asyncio.Event()

    async def worker() -> None:
        try:
            await asyncio.Event().wait()
        finally:
            cleaned_up.set()

    task = asyncio.create_task(worker())
    await asyncio.sleep(0)

    await app_main._cancel_tasks(task)

    assert cleaned_up.is_set()
    assert task.cancelled()


@pytest.mark.asyncio
async def test_close_outbound_clients_closes_provider_after_existing_client_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    async def close_http_client() -> None:
        events.append("shared")
        raise RuntimeError("shared client failed")

    async def close_provider_transport() -> None:
        events.append("provider")

    import app.services.capture_provider_transport as provider_transport
    import app.services.moonraker as moonraker

    monkeypatch.setattr(moonraker, "close_http_client", close_http_client)
    monkeypatch.setattr(
        provider_transport, "close_provider_transport", close_provider_transport
    )

    with pytest.raises(RuntimeError, match="shared client failed"):
        await app_main._close_outbound_clients()

    assert events == ["shared", "provider"]


@pytest.mark.asyncio
async def test_close_outbound_clients_logs_provider_close_failure(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    async def close_provider_transport() -> None:
        raise RuntimeError("provider close failed")

    import app.services.capture_provider_transport as provider_transport
    import app.services.moonraker as moonraker

    monkeypatch.setattr(moonraker, "close_http_client", lambda: _done())
    monkeypatch.setattr(
        provider_transport, "close_provider_transport", close_provider_transport
    )

    with caplog.at_level(logging.ERROR, logger=app_main.logger.name):
        await app_main._close_outbound_clients()

    assert "failed to close capture provider transport" in caplog.text
    assert "RuntimeError" in caplog.text
    assert "provider close failed" not in caplog.text


async def _done() -> None:
    pass


def _fake_request(
    path: str = "/x", headers: dict[str, str] | None = None
) -> StarletteRequest:
    raw_headers = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    scope = {
        "type": "http",
        "method": "GET",
        "path": path,
        "headers": raw_headers,
        "client": ("127.0.0.1", 12345),
        "query_string": b"",
    }
    return StarletteRequest(scope)


@pytest.mark.asyncio
async def test_unhandled_exception_handler_logs_traceback_in_debug(
    caplog: pytest.LogCaptureFixture,
) -> None:
    _overlay["log_level"] = "DEBUG"
    try:
        request = _fake_request(path="/boom")
        with caplog.at_level(logging.ERROR, logger=app_main.logger.name):
            response = await app_main.unhandled_exception_handler(
                request, ValueError("boom")
            )
    finally:
        _overlay.pop("log_level", None)
    assert response.status_code == 500
    # DEBUG branch logs with a traceback (exc_info); confirm it actually ran
    # logger.exception, not just logger.error.
    assert any(record.exc_info for record in caplog.records)


@pytest.mark.asyncio
async def test_unhandled_exception_handler_logs_summary_outside_debug(
    caplog: pytest.LogCaptureFixture,
) -> None:
    _overlay["log_level"] = "INFO"
    try:
        request = _fake_request(path="/boom")
        with caplog.at_level(logging.ERROR, logger=app_main.logger.name):
            response = await app_main.unhandled_exception_handler(
                request, ValueError("boom")
            )
    finally:
        _overlay.pop("log_level", None)
    assert response.status_code == 500
    # Non-debug branch logs the error class name, no traceback.
    assert not any(record.exc_info for record in caplog.records)
    assert any("ValueError" in record.getMessage() for record in caplog.records)


@pytest.mark.asyncio
async def test_bind_audit_context_ignores_non_numeric_token_sub(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict = {}

    def _fake_set_audit_context(*, actor_id, ip):
        captured["actor_id"] = actor_id
        captured["ip"] = ip

    monkeypatch.setattr(app_main, "set_audit_context", _fake_set_audit_context)
    monkeypatch.setattr(app_main, "clear_audit_context", lambda: None)

    import app.services.auth as auth_module

    monkeypatch.setattr(
        auth_module, "verify_access_token", lambda _token: {"sub": "not-an-int"}
    )

    request = _fake_request(headers={"authorization": "Bearer whatever"})

    async def call_next(_request):
        from starlette.responses import Response

        return Response(status_code=200)

    response = await app_main.bind_audit_context(request, call_next)
    assert response.status_code == 200
    # A non-numeric "sub" claim must not blow up the middleware; actor_id
    # falls back to None instead of propagating a ValueError/TypeError.
    assert captured["actor_id"] is None


def test_refresh_printer_gauge_populates_from_db(db_session) -> None:
    printer = Printer(
        name="Gauge Printer",
        moonraker_url="http://gauge.local:7125",
        provider=PrinterProvider.MOONRAKER,
        status=PrinterStatus.READY,
    )
    db_session.add(printer)
    db_session.commit()

    app_main._refresh_printer_gauge()

    sample_found = any(
        sample.labels.get("provider") == "moonraker"
        and sample.labels.get("status") == "ready"
        and sample.value >= 1
        for metric in app_main.printer_status.collect()
        for sample in metric.samples
    )
    assert sample_found


def test_refresh_printer_gauge_survives_db_error(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def _boom():
        raise RuntimeError("db down")

    monkeypatch.setattr(app_main, "get_session_factory", _boom)
    with caplog.at_level(logging.ERROR, logger=app_main.logger.name):
        app_main._refresh_printer_gauge()  # must not raise
    assert any(
        "failed to refresh printer gauge" in r.getMessage() for r in caplog.records
    )


def test_refresh_fleet_gauges_survives_db_error(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def _boom():
        raise RuntimeError("db down")

    monkeypatch.setattr(app_main, "get_session_factory", _boom)
    with caplog.at_level(logging.ERROR, logger=app_main.logger.name):
        app_main._refresh_fleet_gauges()  # must not raise
    assert any(
        "failed to refresh fleet gauges" in r.getMessage() for r in caplog.records
    )


def test_run_due_external_scans_noop_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import external_library, runtime_config

    monkeypatch.setattr(
        runtime_config, "external_libraries_enabled", lambda _session: False
    )
    called = {"scan": False}
    monkeypatch.setattr(
        external_library, "scan_library", lambda _id: called.__setitem__("scan", True)
    )

    app_main._run_due_external_scans()

    assert called["scan"] is False


def test_run_due_external_scans_logs_and_continues_on_scan_failure(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    from app.services import external_library, runtime_config

    monkeypatch.setattr(
        runtime_config, "external_libraries_enabled", lambda _session: True
    )
    monkeypatch.setattr(
        external_library, "libraries_due_for_scan", lambda _session: [1, 2]
    )
    scanned: list[int] = []

    def _scan(library_id: int) -> None:
        scanned.append(library_id)
        if library_id == 1:
            raise RuntimeError("scan blew up")

    monkeypatch.setattr(external_library, "scan_library", _scan)

    with caplog.at_level(logging.ERROR, logger=app_main.logger.name):
        app_main._run_due_external_scans()

    # Both libraries are attempted even though the first raised.
    assert scanned == [1, 2]
    assert any(
        "scheduled scan failed for library 1" in r.getMessage() for r in caplog.records
    )


def test_lifespan_logs_reconciliation_warnings_and_survives_watcher_failure(
    _local_storage: None,
    db_session,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Every reconcile_* call returning a nonzero count logs a warning, and a
    watcher startup failure must not abort the rest of lifespan startup."""
    from app.services import external_library, inbox, jobs, library_watcher, vault_audit

    monkeypatch.setattr(external_library, "reset_orphaned_scans", lambda _session: 2)
    monkeypatch.setattr(jobs, "reconcile_interrupted_jobs", lambda: 3)
    monkeypatch.setattr(vault_audit, "reconcile_interrupted_runs", lambda: 4)
    monkeypatch.setattr(inbox, "reconcile_interrupted_items", lambda: 5)
    monkeypatch.setattr(app_main, "reconcile_stranded_dispatches", lambda: 6)

    async def _boom_start_all(self):
        raise RuntimeError("watcher init failed")

    monkeypatch.setattr(library_watcher.LibraryWatcher, "start_all", _boom_start_all)

    # This test isn't exercising the fleet scheduler; keep it from racing the
    # warning assertions while retaining the production composition signature.
    async def _noop_scheduler(_task_queue, _provider_builder) -> None:
        return None

    monkeypatch.setattr(app_main, "run_fleet_scheduler", _noop_scheduler)

    from app.main import app

    with caplog.at_level(logging.WARNING, logger=app_main.logger.name):
        with TestClient(app):
            pass

    messages = [r.getMessage() for r in caplog.records]
    assert any("reset 2 external library scan" in m for m in messages)
    assert any("reconciled 3 interrupted background job" in m for m in messages)
    assert any("reconciled 4 interrupted vault audit" in m for m in messages)
    assert any("reconciled 5 interrupted pending import" in m for m in messages)
    assert any("reconciled 6 stranded fleet dispatch" in m for m in messages)
    # The watcher exception is swallowed (best-effort) rather than propagating
    # and aborting startup — app.state.library_watcher still gets set.
    assert any("library watcher failed to start" in m for m in messages)


@pytest.mark.asyncio
async def test_gc_loop_logs_and_continues_past_each_task_failure(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """_gc_loop runs GC, delivery pruning, and inbox pruning independently —
    one failing must not prevent the other two from running."""
    import asyncio

    from app.services import inbox, notifications

    monkeypatch.setattr(
        app_main,
        "gc_soft_deleted",
        lambda: (_ for _ in ()).throw(RuntimeError("gc fail")),
    )
    monkeypatch.setattr(
        notifications,
        "prune_deliveries",
        lambda: (_ for _ in ()).throw(RuntimeError("prune fail")),
    )
    monkeypatch.setattr(
        inbox,
        "prune_history",
        lambda: (_ for _ in ()).throw(RuntimeError("history fail")),
    )

    with caplog.at_level(logging.ERROR, logger=app_main.logger.name):
        task = asyncio.create_task(app_main._gc_loop())
        # One pass runs immediately (no initial sleep). Each step is a real
        # asyncio.to_thread() round-trip, so a fixed short sleep is flaky
        # under CI load — poll for all three log lines instead, bounded by
        # a generous timeout, before cancelling ahead of sleep(3600).
        deadline = asyncio.get_event_loop().time() + 5
        expected = {
            "scheduled GC failed",
            "notification delivery pruning failed",
            "pending import history pruning failed",
        }
        while asyncio.get_event_loop().time() < deadline:
            messages = [r.getMessage() for r in caplog.records]
            if all(any(exp in m for m in messages) for exp in expected):
                break
            await asyncio.sleep(0.01)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    messages = [r.getMessage() for r in caplog.records]
    assert any("scheduled GC failed" in m for m in messages)
    assert any("notification delivery pruning failed" in m for m in messages)
    assert any("pending import history pruning failed" in m for m in messages)


@pytest.mark.asyncio
async def test_gc_loop_never_runs_storage_maintenance_when_unconfigured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called = False
    real_sleep = asyncio.sleep

    def gc() -> None:
        nonlocal called
        called = True

    async def stop_after_first_tick(_seconds: float) -> None:
        await real_sleep(0)
        raise asyncio.CancelledError

    monkeypatch.setattr(app_main, "gc_soft_deleted", gc)
    monkeypatch.setattr(app_main.asyncio, "sleep", stop_after_first_tick)

    with pytest.raises(asyncio.CancelledError):
        await app_main._gc_loop(storage_maintenance_enabled=False)

    assert called is False


@pytest.mark.asyncio
async def test_external_scan_loop_skips_during_restore_then_logs_scan_failure(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """_external_scan_loop must (a) skip a tick entirely while a restore is in
    progress, (b) log-and-continue if the scan tick itself raises, and (c)
    release every admitted mutation slot."""
    import asyncio

    _real_sleep = asyncio.sleep

    # Collapse the loop's per-tick 60s sleep so several iterations happen fast.
    monkeypatch.setattr(app_main.asyncio, "sleep", lambda _s: _real_sleep(0))

    calls = {
        "admission_checks": 0,
        "admitted": 0,
        "scan_calls": 0,
        "mutation_ends": 0,
    }

    def _begin_mutating_operation() -> bool:
        calls["admission_checks"] += 1
        # First tick: restore in progress (must skip). Second+ tick: clear.
        admitted = calls["admission_checks"] > 1
        if admitted:
            calls["admitted"] += 1
        return admitted

    def _end_mutating_operation() -> None:
        calls["mutation_ends"] += 1

    def _run_due_external_scans() -> None:
        calls["scan_calls"] += 1
        raise RuntimeError("scan tick blew up")

    monkeypatch.setattr(app_main, "begin_mutating_operation", _begin_mutating_operation)
    monkeypatch.setattr(app_main, "end_mutating_operation", _end_mutating_operation)
    monkeypatch.setattr(app_main, "_run_due_external_scans", _run_due_external_scans)

    with caplog.at_level(logging.ERROR, logger=app_main.logger.name):
        task = asyncio.create_task(app_main._external_scan_loop())
        # Let a few ticks run before cancelling.
        for _ in range(5):
            await asyncio.sleep(0)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    # Skipped on the first tick (restore in progress): scan must not have run then.
    assert calls["admission_checks"] >= 2
    assert calls["scan_calls"] >= 1
    # Cancellation may land after admission but before the worker thread starts;
    # every admitted slot must still be released exactly once.
    assert calls["mutation_ends"] == calls["admitted"]
    assert any(
        "external library scan tick failed" in r.getMessage() for r in caplog.records
    )
