"""Bounded host-side validated geometry, separate from device allocations."""

import time
from collections import OrderedDict
from dataclasses import dataclass
from threading import Lock

from .contracts import ComputeUnavailable, Reason

INPUT_QUEUE_BYTES = 192 * 1024**2
INPUT_CACHE_BYTES = 128 * 1024**2
OUTPUT_QUEUE_BYTES = 192 * 1024**2


@dataclass
class GeometryEntry:
    prepared: object
    size: int
    counts: tuple[int, int, int]
    used: float
    pins: int = 0


class GeometryCache:
    def __init__(self, capacity: int = INPUT_CACHE_BYTES):
        self.capacity = capacity
        self.entries = OrderedDict()
        self.lock = Lock()
        self.hits = self.transferred = 0

    @property
    def used(self):
        with self.lock:
            return sum(e.size for e in self.entries.values())

    def acquire(self, key, size, counts):
        with self.lock:
            self._expire()
            entry = self.entries.get(key)
            if entry is None:
                return None
            if entry.size != size or entry.counts != counts:
                raise ValueError("compute_geometry_reference")
            entry.pins += 1
            entry.used = time.monotonic()
            self.entries.move_to_end(key)
            self.hits += 1
            return entry

    def insert(self, key, size, counts, prepared):
        with self.lock:
            self._expire()
            self.transferred += size
            entry = self.entries.get(key)
            if entry is None:
                if size > self.capacity:
                    raise ComputeUnavailable(Reason.CAPACITY)
                for candidate in list(self.entries):
                    if (
                        sum(e.size for e in self.entries.values()) + size
                        <= self.capacity
                    ):
                        break
                    if not self.entries[candidate].pins:
                        del self.entries[candidate]
                if sum(e.size for e in self.entries.values()) + size > self.capacity:
                    raise ComputeUnavailable(Reason.CAPACITY)
                entry = GeometryEntry(prepared, size, counts, time.monotonic())
                self.entries[key] = entry
            if entry.size != size or entry.counts != counts:
                raise ValueError("compute_geometry_reference")
            entry.pins += 1
            entry.used = time.monotonic()
            self.entries.move_to_end(key)
            return entry

    def release(self, entry):
        with self.lock:
            if entry.pins <= 0:
                raise RuntimeError("compute_geometry_unbalanced_pin")
            entry.pins -= 1

    def _expire(self):
        now = time.monotonic()
        for key, entry in list(self.entries.items()):
            if not entry.pins and now - entry.used >= 60:
                del self.entries[key]
