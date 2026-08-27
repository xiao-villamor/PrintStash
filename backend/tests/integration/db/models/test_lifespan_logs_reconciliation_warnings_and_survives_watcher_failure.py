"""Defends lifespan logs reconciliation warnings and survives watcher failure at the db models integration boundary.

A regression could commit partial, unauthenticated, or internally inconsistent database state.
"""

from __future__ import annotations

from ._main_lifespan_shared import (
    TestClient,
    app_main,
    asyncio,
    logging,
    pytest,
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
