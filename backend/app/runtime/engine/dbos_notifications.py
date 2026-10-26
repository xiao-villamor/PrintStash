"""Wakeup hints for the isolated, version-pinned SQLite DBOS experiment.

Only the experimental SDK patch calls this module. Work remains durable in
DBOS; signals merely interrupt its dispatcher sleep. Rate-limited queues keep
timed checks, and other queues scan at least every 30 seconds after a lost hint.
No extra thread is started, including in native worker subprocesses.
"""

from __future__ import annotations

import threading
import time

RECOVERY_SECONDS = 30.0


class QueueSignals:
    """Coalesced per-queue notifications; consume before the durable claim."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._signals: dict[str, tuple[threading.Condition, list[bool]]] = {}

    def _signal(self, name: str) -> tuple[threading.Condition, list[bool]]:
        with self._lock:
            if name not in self._signals:
                self._signals[name] = (threading.Condition(), [False])
            return self._signals[name]

    def notify(self, name: str | None) -> None:
        if name is None:
            return
        condition, pending = self._signal(name)
        with condition:
            pending[0] = True
            condition.notify()

    def notify_all(self) -> None:
        with self._lock:
            names = tuple(self._signals)
        for name in names:
            self.notify(name)

    def wait(self, name: str, stop: threading.Event, seconds: float) -> bool:
        if seconds <= 0:
            raise ValueError("recovery interval must be positive")
        condition, pending = self._signal(name)
        deadline = time.monotonic() + seconds
        with condition:
            while not stop.is_set() and not pending[0]:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                # DBOS owns the stop Event. This in-memory check starts no SQL
                # pass; it only bounds shutdown when no queue hint arrives.
                condition.wait(min(remaining, 1.0))
            pending[0] = False
        return stop.is_set()


signals = QueueSignals()
