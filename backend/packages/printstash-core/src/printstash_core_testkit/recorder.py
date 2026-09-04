"""Thread-safe in-process recorder for protocol fakes."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Received:
    target: str
    method: str
    path: str
    headers: dict[str, str]
    json: Any | None = None
    body: bytes | None = None
    status_returned: int = 200


class Recorder:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._items: list[Received] = []
        self._counters: dict[str, int] = {}

    def record(self, item: Received) -> None:
        with self._lock:
            self._items.append(item)

    def for_target(self, target: str) -> list[Received]:
        with self._lock:
            return [item for item in self._items if item.target == target]

    def all(self) -> list[Received]:
        with self._lock:
            return list(self._items)

    def clear(self) -> None:
        with self._lock:
            self._items.clear()
            self._counters.clear()

    def bump(self, key: str) -> int:
        with self._lock:
            self._counters[key] = self._counters.get(key, 0) + 1
            return self._counters[key]
