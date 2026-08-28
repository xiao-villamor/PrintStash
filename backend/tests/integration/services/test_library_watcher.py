"""Unit tests for app.services.library_watcher: had almost no direct coverage
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
from app.services import library_watcher as lw
from app.services import runtime_config


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
    def test_refresh_follows_the_configured_set_of_watchers(
        self, db_session: Session, tmp_path: Path
    ) -> None:
        _enable_external_libraries(db_session)
        lib = ExternalLibrary(
            name="Lib",
            root_path=str(tmp_path),
            watch_mode=ExternalLibraryWatchMode.EVENTS,
        )
        db_session.add(lib)
        db_session.commit()
        db_session.refresh(lib)

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
        lib = ExternalLibrary(
            name="Lib",
            root_path=str(root_a),
            watch_mode=ExternalLibraryWatchMode.EVENTS,
        )
        db_session.add(lib)
        db_session.commit()
        db_session.refresh(lib)

        watcher = lw.LibraryWatcher()

        async def _run() -> None:
            await watcher.refresh()
            assert watcher.watched_roots[lib.id] == str(root_a)

            with lw.get_session_factory().session() as s:
                row = s.get(ExternalLibrary, lib.id)
                row.root_path = str(root_b)
                s.add(row)
                s.commit()

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
        lib = ExternalLibrary(
            name="Lib",
            root_path=str(tmp_path),
            watch_mode=ExternalLibraryWatchMode.EVENTS,
        )
        db_session.add(lib)
        db_session.commit()
        db_session.refresh(lib)

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
        lib = ExternalLibrary(
            name="Lib",
            root_path=str(tmp_path),
            watch_mode=ExternalLibraryWatchMode.EVENTS,
        )
        db_session.add(lib)
        db_session.commit()
        db_session.refresh(lib)

        monkeypatch.setattr(
            "app.services.external_library.detect_fs_kind", lambda _path: "network"
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
            "app.services.external_library.detect_fs_kind", lambda _path: "network"
        )

        watcher = lw.LibraryWatcher()
        assert watcher._compute_desired() == {}  # noqa: SLF001


class TestStopWatcher:
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


class TestDebouncedScan:
    def test_debounced_scan_coalesces_a_burst_of_events(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        calls: list[int] = []
        monkeypatch.setattr(
            lw.external_library, "scan_library", lambda lib_id: calls.append(lib_id)
        )

        watcher = lw.LibraryWatcher()

        async def _run() -> None:
            watcher._schedule_scan(7)  # noqa: SLF001
            await asyncio.sleep(0.01)
            watcher._schedule_scan(7)  # noqa: SLF001  - cancels the pending task, reschedules
            await asyncio.sleep(0.01)
            watcher._schedule_scan(7)  # noqa: SLF001
            await asyncio.sleep(0.2)  # past the 0.05s debounce

        asyncio.run(_run())
        assert calls == [7]  # only one scan for the whole burst

    def test_debounced_scan_requeues_when_a_change_lands_mid_scan(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        calls: list[int] = []

        def _slow_scan(lib_id: int) -> None:
            calls.append(lib_id)

        monkeypatch.setattr(lw.external_library, "scan_library", _slow_scan)

        watcher = lw.LibraryWatcher()
        watcher.tasks[9] = (
            None  # present so the requeue branch's "in self.tasks" check passes
        )

        async def _run() -> None:
            # First scan in flight (marked "scanning") ...
            watcher._scanning.add(9)  # noqa: SLF001
            watcher._schedule_scan(9)  # noqa: SLF001
            await asyncio.sleep(
                0.1
            )  # debounce elapses; _debounced_scan sees "already scanning"
            assert 9 in watcher._rescan_requested  # noqa: SLF001
            assert calls == []  # scan was deferred, not run, while "scanning"

            watcher._scanning.discard(9)  # noqa: SLF001
            # Nothing re-triggers automatically here (that happens at the end of a
            # real scan's finally block) — simulate that completion explicitly.
            watcher._rescan_requested.discard(9)  # noqa: SLF001
            watcher._schedule_scan(9)  # noqa: SLF001
            await asyncio.sleep(0.1)
            assert calls == [9]

        asyncio.run(_run())

    def test_real_file_create_triggers_a_scheduled_scan(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[int] = []
        monkeypatch.setattr(
            lw.external_library, "scan_library", lambda lib_id: calls.append(lib_id)
        )

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
