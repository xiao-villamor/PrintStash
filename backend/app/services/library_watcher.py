"""Background worker: watch external-library folders for real-time changes.

For each external library whose ``watch_mode`` resolves to "watch" (see
``external_library.should_watch``), we keep one ``watchfiles.awatch`` task.
A burst of filesystem events is debounced and then simply triggers the existing
``external_library.scan_library`` reconcile — the watcher never re-implements
indexing, so all of the scan's safety guards (abort on unmounted/empty root, no
mass-delete) apply unchanged.

Watching only works on local filesystems; on network mounts (NFS/SMB/CIFS) the
kernel does not deliver inotify events. ``AUTO`` libraries on a network root are
left to their scheduled scan. When the user forces ``EVENTS`` on a non-local
root we fall back to watchfiles' own stat-polling (``force_polling``) so the
feature still works, just less efficiently.

A failed watcher never blocks startup or crashes the app: errors are logged and
the library falls back to schedule-only.
"""

from __future__ import annotations

import asyncio
from typing import Dict, Optional, Set

from fastapi import Request
from sqlmodel import select

from app.core.logging import get_logger
from app.db.models import ExternalLibrary
from app.db.session import get_session_factory
from app.services import external_library
from app.services.runtime_config import external_libraries_enabled

logger = get_logger(__name__)

# Coalesce a burst of file events for this long before triggering a reconcile.
_DEBOUNCE_S = 10.0
# How often the supervisor re-syncs running watchers against the DB.
_SUPERVISOR_INTERVAL_S = 45.0


class LibraryWatcher:
    def __init__(self) -> None:
        self.tasks: Dict[int, asyncio.Task] = {}
        self.stop_events: Dict[int, asyncio.Event] = {}
        # The root currently being watched per library (to detect path edits).
        self.watched_roots: Dict[int, str] = {}
        self._pending: Dict[int, asyncio.Task] = {}
        self._scanning: Set[int] = set()
        self._rescan_requested: Set[int] = set()
        self._supervisor: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()

    # -- lifecycle --

    async def start_all(self) -> None:
        await self.refresh()
        if self._supervisor is None:
            self._supervisor = asyncio.create_task(
                self._supervise(), name="library-watcher-supervisor"
            )

    async def stop_all(self) -> None:
        if self._supervisor is not None:
            self._supervisor.cancel()
            self._supervisor = None
        async with self._lock:
            ids = list(self.tasks.keys())
        for lib_id in ids:
            await self._stop_watcher(lib_id)

    async def _supervise(self) -> None:
        while True:
            try:
                await asyncio.sleep(_SUPERVISOR_INTERVAL_S)
                await self.refresh()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("library watcher supervisor tick failed")

    async def refresh(self) -> None:
        """Reconcile running watchers with current library config.

        Starts watchers that should run, stops those that shouldn't, and restarts
        any whose root path changed. Safe to call repeatedly (API edits, ticks).
        """
        from app.services.backup import restore_in_progress

        if restore_in_progress():
            return
        desired = await asyncio.to_thread(self._compute_desired)

        async with self._lock:
            running = set(self.tasks.keys())
        desired_ids = set(desired.keys())

        for lib_id in running - desired_ids:
            await self._stop_watcher(lib_id)

        for lib_id, (root, force_polling) in desired.items():
            if lib_id in self.tasks and self.watched_roots.get(lib_id) != root:
                await self._stop_watcher(lib_id)  # root changed → restart
            if lib_id not in self.tasks:
                await self._start_watcher(lib_id, root, force_polling)

    def _compute_desired(self) -> Dict[int, tuple[str, bool]]:
        """{library_id: (root, force_polling)} for libraries that should be watched.

        Also persists the freshly detected ``fs_kind`` so the UI can explain why
        watching is or isn't active. Runs in a worker thread (sync DB + /proc IO).
        """
        desired: Dict[int, tuple[str, bool]] = {}
        with get_session_factory().session() as session:
            if not external_libraries_enabled(session):
                return desired
            libs = session.exec(
                select(ExternalLibrary).where(ExternalLibrary.enabled == True)  # noqa: E712
            ).all()
            for lib in libs:
                if lib.id is None:
                    continue
                fs_kind = external_library.detect_fs_kind(lib.root_path)
                if lib.fs_kind != fs_kind:
                    lib.fs_kind = fs_kind
                    session.add(lib)
                if external_library.should_watch(lib, fs_kind):
                    desired[lib.id] = (lib.root_path, fs_kind != "local")
            session.commit()
        return desired

    async def _start_watcher(
        self, library_id: int, root: str, force_polling: bool
    ) -> None:
        async with self._lock:
            if library_id in self.tasks:
                return
            stop = asyncio.Event()
            self.stop_events[library_id] = stop
            self.watched_roots[library_id] = root
            self.tasks[library_id] = asyncio.create_task(
                self._run_watcher(library_id, root, force_polling, stop),
                name=f"library-watch-{library_id}",
            )
        logger.info(
            "watching external library %s at %s (polling=%s)",
            library_id,
            root,
            force_polling,
        )

    async def _stop_watcher(self, library_id: int) -> None:
        async with self._lock:
            stop = self.stop_events.pop(library_id, None)
            task = self.tasks.pop(library_id, None)
            self.watched_roots.pop(library_id, None)
            pending = self._pending.pop(library_id, None)
        if stop:
            stop.set()
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.exception("library watcher exit error for %s", library_id)
        if pending:
            pending.cancel()

    # -- worker --

    async def _run_watcher(
        self, library_id: int, root: str, force_polling: bool, stop: asyncio.Event
    ) -> None:
        from watchfiles import awatch

        try:
            async for _changes in awatch(
                root,
                recursive=True,
                stop_event=stop,
                force_polling=force_polling,
            ):
                self._schedule_scan(library_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            # Never crash the app over a watcher failure — fall back to schedule.
            logger.exception(
                "watcher for library %s failed; falling back to scheduled scans",
                library_id,
            )

    # -- debounced scan --

    def _schedule_scan(self, library_id: int) -> None:
        existing = self._pending.get(library_id)
        if existing and not existing.done():
            existing.cancel()
        self._pending[library_id] = asyncio.create_task(
            self._debounced_scan(library_id)
        )

    async def _debounced_scan(self, library_id: int) -> None:
        from app.services.backup import (
            begin_mutating_operation,
            end_mutating_operation,
        )

        try:
            await asyncio.sleep(_DEBOUNCE_S)
        except asyncio.CancelledError:
            return
        if not begin_mutating_operation():
            return
        # Don't overlap scans for the same library; remember to rescan after.
        if library_id in self._scanning:
            self._rescan_requested.add(library_id)
            end_mutating_operation()
            return
        self._scanning.add(library_id)
        try:
            await asyncio.to_thread(external_library.scan_library, library_id)
        except Exception:
            logger.exception("watched scan failed for library %s", library_id)
        finally:
            self._scanning.discard(library_id)
            end_mutating_operation()
        # Catch changes that landed while the previous scan was running.
        if library_id in self._rescan_requested and library_id in self.tasks:
            self._rescan_requested.discard(library_id)
            self._schedule_scan(library_id)


def get_library_watcher(request: Request) -> LibraryWatcher:
    """FastAPI dependency: returns the LibraryWatcher stored on app.state."""
    return request.app.state.library_watcher
