"""Unit tests for app.modules.sources.library_watcher: had almost no direct coverage
(20% per the 0.11 audit) despite driving real filesystem watching.

Debounce/supervisor intervals are monkeypatched down to milliseconds so these
stay fast; the create/modify/delete -> scheduled scan path uses a real
``watchfiles.awatch`` against a tmp_path root (the actual mechanism, not a
re-implementation of it).
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest
from sqlmodel import Session

from app.db.models import ExternalLibrary, ExternalLibraryWatchMode
from app.modules.administration import runtime_config
from app.modules.sources import library_watcher as lw
from tests.factories import build_external_library


@pytest.fixture(autouse=True)
def _fast_debounce(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(lw, "_DEBOUNCE_S", 0.05)
    monkeypatch.setattr(lw, "_SUPERVISOR_INTERVAL_S", 0.05)


def _enable_external_libraries(db_session: Session) -> None:
    runtime_config.set_external_libraries_enabled(db_session, True)


async def _wait_for(condition, *, timeout: float = 5.0) -> None:
    """Wait until `condition()` holds, or give up after `timeout` seconds.

    Sleeping a fixed span instead would be a flake rather than a failure: under a
    parallel run the loop can be starved well past a supervisor tick, and the
    resulting red test says nothing about the behaviour under test.
    """
    deadline = time.monotonic() + timeout
    while not condition() and time.monotonic() < deadline:
        await asyncio.sleep(0.01)


# ---------------------------------------------------------------------------
# _compute_desired
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Debounced scan scheduling
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Real watcher lifecycle against a real tmp_path (actual watchfiles.awatch)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# refresh() reconciliation
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Supervisor
# ---------------------------------------------------------------------------


class TestRefresh:
    def test_configuration_change_wakes_the_supervisor(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(lw, "_SUPERVISOR_INTERVAL_S", 60)
        watcher = lw.LibraryWatcher()

        async def _run() -> None:
            refreshed = asyncio.Event()

            async def refresh() -> None:
                refreshed.set()

            monkeypatch.setattr(watcher, "refresh", refresh)
            await watcher.start_all()
            refreshed.clear()
            try:
                await asyncio.to_thread(watcher.request_refresh)
                await asyncio.wait_for(refreshed.wait(), timeout=5)
            finally:
                await watcher.stop_all()

        asyncio.run(_run())

    def test_refresh_follows_the_configured_set_of_watchers(
        self, db_session: Session, tmp_path: Path
    ) -> None:
        _enable_external_libraries(db_session)
        lib = build_external_library(
            db_session,
            tmp_path,
            name="Lib",
            watch_mode=ExternalLibraryWatchMode.EVENTS,
        )

        watcher = lw.LibraryWatcher()

        async def _run() -> None:
            await watcher.refresh()
            assert lib.id in watcher.tasks

            lib.enabled = False
            with lw.get_session_factory().session() as s:
                row = s.get(ExternalLibrary, lib.id)
                row.enabled = False
                s.add(row)
                s.commit()

            await watcher.refresh()
            assert lib.id not in watcher.tasks

            await watcher.stop_all()

        asyncio.run(_run())

    def test_refresh_restarts_watcher_when_root_path_changes(
        self, db_session: Session, tmp_path: Path
    ) -> None:
        _enable_external_libraries(db_session)
        root_a = tmp_path / "a"
        root_b = tmp_path / "b"
        root_a.mkdir()
        root_b.mkdir()
        lib = build_external_library(
            db_session,
            root_a,
            name="Lib",
            watch_mode=ExternalLibraryWatchMode.EVENTS,
        )

        watcher = lw.LibraryWatcher()

        async def _run() -> None:
            await watcher.refresh()
            assert watcher.watched_roots[lib.id] == str(root_a)

            with lw.get_session_factory().session() as s:
                row = s.get(ExternalLibrary, lib.id)
                row.root_path = str(root_b)
                row.root_identity = None
                s.add(row)
                s.commit()
                from app.modules.sources.root_binding import enroll_external_root

                enroll_external_root(s, row)

            await watcher.refresh()
            assert watcher.watched_roots[lib.id] == str(root_b)

            await watcher.stop_all()

        asyncio.run(_run())

    def test_supervisor_periodically_calls_refresh(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls = []
        watcher = lw.LibraryWatcher()

        async def fake_refresh() -> None:
            calls.append(1)

        monkeypatch.setattr(watcher, "refresh", fake_refresh)

        async def _run() -> None:
            await (
                watcher.start_all()
            )  # calls refresh() once directly, then starts supervisor
            await _wait_for(lambda: len(calls) >= 2)
            await watcher.stop_all()

        asyncio.run(_run())
        assert (
            len(calls) >= 2
        )  # the initial refresh() plus at least one supervisor tick

    def test_supervisor_survives_a_refresh_exception(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        watcher = lw.LibraryWatcher()
        calls = []

        async def flaky_refresh() -> None:
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("boom")

        monkeypatch.setattr(watcher, "refresh", flaky_refresh)

        async def _run() -> None:
            watcher._supervisor = asyncio.create_task(watcher._supervise())  # noqa: SLF001
            await _wait_for(lambda: len(calls) >= 2)

            assert len(calls) >= 2  # survived the RuntimeError and ticked again
            watcher._supervisor.cancel()  # noqa: SLF001
            try:
                await watcher._supervisor  # noqa: SLF001
            except asyncio.CancelledError:
                pass

        asyncio.run(_run())


class TestComputeDesired:
    def test_compute_desired_empty_when_feature_disabled(
        self, db_session: Session, tmp_path: Path
    ) -> None:
        db_session.add(
            ExternalLibrary(
                name="Lib",
                root_path=str(tmp_path),
                watch_mode=ExternalLibraryWatchMode.EVENTS,
            )
        )
        db_session.commit()

        watcher = lw.LibraryWatcher()
        assert watcher._compute_desired() == {}  # noqa: SLF001

    def test_compute_desired_records_the_filesystem_kind_it_detected(
        self, db_session: Session, tmp_path: Path
    ) -> None:
        _enable_external_libraries(db_session)
        lib = build_external_library(
            db_session,
            tmp_path,
            name="Lib",
            watch_mode=ExternalLibraryWatchMode.EVENTS,
        )

        watcher = lw.LibraryWatcher()
        desired = watcher._compute_desired()  # noqa: SLF001

        assert lib.id in desired
        root, _force_polling = desired[lib.id]
        assert root == str(tmp_path)
        db_session.refresh(lib)
        assert lib.fs_kind is not None  # detect_fs_kind's result got persisted

    def test_compute_desired_excludes_watch_mode_off(
        self, db_session: Session, tmp_path: Path
    ) -> None:
        _enable_external_libraries(db_session)
        db_session.add(
            ExternalLibrary(
                name="Lib",
                root_path=str(tmp_path),
                watch_mode=ExternalLibraryWatchMode.OFF,
            )
        )
        db_session.commit()

        watcher = lw.LibraryWatcher()
        assert watcher._compute_desired() == {}  # noqa: SLF001

    def test_compute_desired_forces_polling_for_network_fs(
        self, db_session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _enable_external_libraries(db_session)
        lib = build_external_library(
            db_session,
            tmp_path,
            name="Lib",
            watch_mode=ExternalLibraryWatchMode.EVENTS,
        )

        monkeypatch.setattr(
            "app.modules.sources.external_library.detect_fs_kind",
            lambda _path: "network",
        )

        watcher = lw.LibraryWatcher()
        desired = watcher._compute_desired()  # noqa: SLF001

        _root, force_polling = desired[lib.id]
        assert force_polling is True

    def test_compute_desired_excludes_auto_mode_on_network_fs(
        self, db_session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _enable_external_libraries(db_session)
        db_session.add(
            ExternalLibrary(
                name="Lib",
                root_path=str(tmp_path),
                watch_mode=ExternalLibraryWatchMode.AUTO,
            )
        )
        db_session.commit()

        monkeypatch.setattr(
            "app.modules.sources.external_library.detect_fs_kind",
            lambda _path: "network",
        )

        watcher = lw.LibraryWatcher()
        assert watcher._compute_desired() == {}  # noqa: SLF001


class TestStopWatcher:
    def test_missing_root_reports_scheduled_scan_fallback(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        watcher = lw.LibraryWatcher()
        missing_root = tmp_path / "unmounted"

        async def _run() -> None:
            await asyncio.wait_for(
                watcher._run_watcher(1, str(missing_root), False, asyncio.Event()),  # noqa: SLF001
                timeout=5,
            )

        asyncio.run(_run())

        assert "falling back to scheduled scans" in caplog.text
        assert not missing_root.exists()

    def test_start_watcher_is_idempotent_for_an_active_library(
        self, tmp_path: Path
    ) -> None:
        watcher = lw.LibraryWatcher()

        async def _run() -> None:
            await watcher._start_watcher(1, str(tmp_path), False)  # noqa: SLF001
            original = watcher.tasks[1]

            await watcher._start_watcher(1, str(tmp_path / "other"), True)  # noqa: SLF001

            assert watcher.tasks[1] is original
            assert watcher.watched_roots[1] == str(tmp_path)
            await watcher._stop_watcher(1)  # noqa: SLF001

        asyncio.run(_run())

    def test_a_watchers_task_lifecycle_is_symmetrical(self, tmp_path: Path) -> None:
        watcher = lw.LibraryWatcher()

        async def _run() -> None:
            await watcher._start_watcher(1, str(tmp_path), False)  # noqa: SLF001
            assert 1 in watcher.tasks
            assert 1 in watcher.stop_events
            assert watcher.watched_roots[1] == str(tmp_path)

            await watcher._stop_watcher(1)  # noqa: SLF001
            assert 1 not in watcher.tasks
            assert 1 not in watcher.stop_events
            assert 1 not in watcher.watched_roots

        asyncio.run(_run())


def _record_requests(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Record the scans the watcher asks the scan owner for, without scanning."""
    requested: list[int] = []

    def request_scan(_session: Session, library_id: int, **_kwargs: object) -> None:
        requested.append(library_id)

    monkeypatch.setattr(lw.external_library, "request_scan", request_scan)
    return requested


class TestDebouncedScan:
    def test_debounced_scan_coalesces_a_burst_of_events(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        calls = _record_requests(monkeypatch)

        watcher = lw.LibraryWatcher()

        async def _run() -> None:
            watcher._schedule_scan(7)  # noqa: SLF001
            await asyncio.sleep(0.01)
            watcher._schedule_scan(7)  # noqa: SLF001  - cancels the pending task, reschedules
            await asyncio.sleep(0.01)
            watcher._schedule_scan(7)  # noqa: SLF001
            await asyncio.sleep(0.2)  # past the 0.05s debounce

        asyncio.run(_run())
        assert calls == [7]  # only one request for the whole burst

    def test_records_a_change_that_lands_while_a_scan_runs(
        self, tmp_path: Path, threaded_hub_db: None
    ) -> None:
        from app.db.session import get_session_factory

        with get_session_factory().scoped_session() as session:
            library = build_external_library(
                session, tmp_path / "nas", name="nas", scanning=True
            )
            library_id = library.id
        assert library_id is not None
        watcher = lw.LibraryWatcher()

        async def _run() -> None:
            watcher._schedule_scan(library_id)  # noqa: SLF001
            await asyncio.sleep(0.2)

        asyncio.run(_run())

        # The running scan already took its request; this one is the next scan's.
        with get_session_factory().scoped_session() as session:
            refreshed = session.get(ExternalLibrary, library_id)
            assert refreshed is not None and refreshed.scan_requested_at is not None

    def test_real_file_create_triggers_a_scheduled_scan(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls = _record_requests(monkeypatch)

        watcher = lw.LibraryWatcher()

        async def _run() -> None:
            await watcher._start_watcher(3, str(tmp_path), False)  # noqa: SLF001
            # Give awatch a moment to start its inotify/polling loop before we
            # write, or the very first event can be missed.
            await asyncio.sleep(0.3)
            await asyncio.to_thread(
                (tmp_path / "new_model.stl").write_bytes, b"solid x\n"
            )

            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline and not calls:
                await asyncio.sleep(0.05)

            await watcher._stop_watcher(3)  # noqa: SLF001

        asyncio.run(_run())
        assert calls == [3]
