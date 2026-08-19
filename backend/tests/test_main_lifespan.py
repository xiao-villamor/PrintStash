"""Boots the real app lifespan (startup + shutdown), not just handler-level tests.

Every other test in the suite gets its FastAPI ``app`` fixture pre-wired
(``app.state.printer_hub`` set manually, no ``with TestClient(app) as client``),
so ``app/main.py``'s ``lifespan()`` — DB init, storage init, background task
wiring, graceful shutdown — had no direct coverage (58% per the 0.11 audit).
This starts it for real via Starlette's TestClient context-manager protocol.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request as StarletteRequest

import app.main as app_main
from app.core.config import _overlay
from app.db.models import Printer, PrinterProvider, PrinterStatus, User
from app.services import storage_backend
from app.services.auth import create_access_token, hash_password


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
    from app.services import printer_jobs
    from app.services.task_queue import LocalTaskQueue

    # The production queue is process-wide and may already be bound to an
    # event loop used by an earlier scheduler test. Give this real lifespan a
    # fresh local queue so its scheduler can bind and shut down on this loop.
    monkeypatch.setattr(printer_jobs, "task_queue", LocalTaskQueue())

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
        ):
            assert hasattr(app.state, attr), f"app.state.{attr} not set by lifespan"
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

    # This test isn't exercising the fleet scheduler, and starting a second
    # real run_fleet_scheduler() task in this process trips a pre-existing
    # hazard: app.services.task_queue.task_queue is a module-level singleton
    # whose asyncio.Queue binds to the first event loop that calls get()/put()
    # on it — a second TestClient lifespan on a different loop (as
    # test_lifespan_starts_background_tasks_and_shuts_down_cleanly does in
    # this same file) then fails with "bound to a different event loop".
    # Stub it out here rather than touching that production singleton.
    async def _noop_scheduler() -> None:
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
