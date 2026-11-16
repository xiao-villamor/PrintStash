"""Opt-in wakeup hints for PrintStash's pinned DBOS SQLite adapter.

The build-time SDK patch calls this module; it never replaces DBOS claims,
fencing or durable state. Activation is explicit and verifies the installed
patch before any workers start. Importing this module starts no threads.
"""

from __future__ import annotations

import hashlib
import importlib
import importlib.metadata
import json
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

PATCH_VERSION = 3
RECOVERY_SECONDS = 30.0
RETRY_INITIAL_SECONDS = 0.25
RETRY_MAX_SECONDS = 2.0
PATCH_FILES = frozenset({"_queue.py", "_core.py", "_sys_db.py"})
MANIFEST = "printstash-notifications.json"


class ClaimRetry:
    """Failure backoff is independent of the long idle recovery deadline.

    The worker waits on its stop Event for this delay, so a stream of hints
    cannot turn a database failure into a tight retry loop. Success resets it.
    """

    def __init__(self) -> None:
        self.delay: float | None = None

    def failed(self) -> None:
        self.delay = (
            RETRY_INITIAL_SECONDS
            if self.delay is None
            else min(RETRY_MAX_SECONDS, self.delay * 2)
        )

    def succeeded(self) -> None:
        self.delay = None


@dataclass
class _Signal:
    condition: threading.Condition = field(default_factory=threading.Condition)
    pending: bool = False
    notifications: int = 0
    coalesced: int = 0
    hint_wakes: int = 0
    recovery_scans: int = 0
    failed_passes: int = 0


class QueueSignals:
    """Coalesced local hints; durable claims remain authoritative."""

    def __init__(self, *, enabled: bool = True) -> None:
        self.enabled = enabled
        self._lock = threading.Lock()
        self._signals: dict[str, _Signal] = {}

    def configure(self, *, enabled: bool) -> None:
        """Change mode only after the old DBOS workers have stopped."""
        with self._lock:
            self._signals.clear()
            self.enabled = enabled

    def _signal(self, name: str) -> _Signal:
        with self._lock:
            if name not in self._signals:
                self._signals[name] = _Signal()
            return self._signals[name]

    def notify(self, name: str | None) -> None:
        if name is None or not self.enabled:
            return
        state = self._signal(name)
        with state.condition:
            state.notifications += 1
            state.coalesced += int(state.pending)
            state.pending = True
            state.condition.notify()

    def notify_all(self) -> None:
        if not self.enabled:
            return
        with self._lock:
            names = tuple(self._signals)
        for name in names:
            self.notify(name)

    def failed(self, name: str) -> None:
        state = self._signal(name)
        with state.condition:
            state.failed_passes += 1

    def wait(self, name: str, stop: threading.Event, seconds: float) -> bool:
        if seconds <= 0:
            raise ValueError("recovery interval must be positive")
        state = self._signal(name)
        deadline = time.monotonic() + seconds
        with state.condition:
            while not stop.is_set() and not state.pending:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                # In-memory stop checks execute no SQL and start no threads.
                state.condition.wait(min(remaining, 1.0))
            if not stop.is_set():
                state.hint_wakes += int(state.pending)
                state.recovery_scans += int(not state.pending)
            state.pending = False
        return stop.is_set()

    def snapshot(self) -> dict[str, dict[str, int]]:
        """Cheap lifecycle diagnostics; no background sampler or database I/O."""
        with self._lock:
            states = tuple(self._signals.items())
        result = {}
        for name, state in states:
            with state.condition:
                result[name] = {
                    "notifications": state.notifications,
                    "coalesced": state.coalesced,
                    "hint_wakes": state.hint_wakes,
                    "recovery_scans": state.recovery_scans,
                    "failed_passes": state.failed_passes,
                }
        return result


def verify_patch() -> None:
    """Refuse absent, partially installed or drifted SDK patches on opt-in."""
    sdk = importlib.import_module("dbos")
    if sdk.__file__ is None:
        raise RuntimeError("dbos_notification_patch_missing_or_incompatible")
    root = Path(sdk.__file__).resolve().parent
    try:
        manifest = json.loads((root / MANIFEST).read_text())
        valid = (
            importlib.metadata.version("dbos") == "3.5.0"
            and manifest["patch_version"] == PATCH_VERSION
            and set(manifest["files"]) == PATCH_FILES
            and manifest["adapter_sha256"]
            == hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        )
        for name in PATCH_FILES:
            loaded = importlib.import_module("dbos." + name.removesuffix(".py"))
            source = loaded.__file__
            valid = valid and (
                source is not None
                and Path(source).resolve() == root / name
                and loaded.PRINTSTASH_NOTIFICATION_PATCH == PATCH_VERSION
                and hashlib.sha256((root / name).read_bytes()).hexdigest()
                == manifest["files"][name]
            )
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        raise RuntimeError("dbos_notification_patch_missing_or_incompatible") from exc
    if not valid:
        raise RuntimeError("dbos_notification_patch_missing_or_incompatible")


signals = QueueSignals(enabled=False)
