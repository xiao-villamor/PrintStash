"""Single-owner byte ledger with idle LRU eviction and pinned active resources."""

from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass

from .contracts import ComputeUnavailable, Reason


@dataclass
class Entry:
    size: int
    release: Callable[[], None]
    last_used: float
    pins: int = 0


class Residency:
    def __init__(self, capacity: int):
        if capacity <= 0:
            raise ValueError("compute_capacity_invalid")
        self.capacity = capacity
        self.entries: OrderedDict[str, Entry] = OrderedDict()

    @property
    def used(self) -> int:
        return sum(entry.size for entry in tuple(self.entries.values()))

    def reserve(
        self, key: str, size: int, release: Callable[[], None], now: float
    ) -> None:
        if key in self.entries or size <= 0:
            raise ValueError("compute_reservation_invalid")
        self.make_room(size)
        self.entries[key] = Entry(size, release, now)

    def grow(self, key: str, size: int) -> None:
        entry = self.entries[key]
        if size <= entry.size:
            return
        # Protect the resident allocation while admitting only its extra peak.
        entry.pins += 1
        try:
            self.make_room(size - entry.size)
            entry.size = size
        finally:
            entry.pins -= 1

    def make_room(self, size: int) -> None:
        if size < 0 or size > self.capacity:
            raise ComputeUnavailable(Reason.CAPACITY)
        for key in sorted(
            self.entries, key=lambda key: not key.startswith("geometry:")
        ):
            if self.used + size <= self.capacity:
                break
            if not self.entries[key].pins:
                self.remove(key)
        if self.used + size > self.capacity:
            raise ComputeUnavailable(Reason.CAPACITY)

    def pin(self, key: str, now: float) -> None:
        entry = self.entries[key]
        entry.pins += 1
        entry.last_used = now
        self.entries.move_to_end(key)

    def unpin(self, key: str) -> None:
        entry = self.entries[key]
        if entry.pins <= 0:
            raise RuntimeError("compute_unbalanced_pin")
        entry.pins -= 1

    def remove(self, key: str) -> None:
        entry = self.entries[key]
        if entry.pins:
            raise RuntimeError("compute_resource_in_use")
        entry.release()
        del self.entries[key]

    def expire(self, now: float, idle_seconds: float = 300) -> None:
        for key, entry in list(self.entries.items()):
            timeout = (
                min(60, idle_seconds) if key.startswith("geometry:") else idle_seconds
            )
            if not entry.pins and now - entry.last_used >= timeout:
                self.remove(key)


class QueueBudget:
    """Bound concurrent receive/parse/execution frames before accepting their body."""

    def __init__(self, capacity: int):
        from threading import Lock

        self.capacity = capacity
        self.used = 0
        self.lock = Lock()

    def reserve(self, size: int) -> None:
        with self.lock:
            if size < 0 or self.used + size > self.capacity:
                raise ComputeUnavailable(Reason.CAPACITY)
            self.used += size

    def release(self, size: int) -> None:
        with self.lock:
            if size < 0 or size > self.used:
                raise RuntimeError("compute_unbalanced_queue")
            self.used -= size
