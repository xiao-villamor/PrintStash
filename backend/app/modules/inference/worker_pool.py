"""Bounded reuse of monitored ONNX children; no queries or vectors live here."""

from __future__ import annotations

import atexit
import subprocess
import threading
import time
from collections import OrderedDict
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from printstash_core.inference import EmbeddingError
from printstash_core.inference.context import InferenceContext


@dataclass
class Worker:
    process: subprocess.Popen
    directory: Path
    busy: bool = False
    touched: float = 0
    waiting_queries: int = 0
    warmed: bool = False

    def close(self):
        if self.process.poll() is None:
            self.process.kill()
        self.process.wait()
        for stream in (self.process.stdin, self.process.stdout):
            if stream is not None:
                stream.close()


class WorkerPool:
    def __init__(self, capacity: int = 2, idle_seconds: float = 300):
        if not 1 <= capacity <= 2 or not 1 <= idle_seconds <= 3600:
            raise ValueError("embedding_worker_cache_invalid")
        self.capacity, self.idle_seconds = capacity, idle_seconds
        self._condition = threading.Condition()
        self._workers: OrderedDict[tuple, Worker] = OrderedDict()
        self._protected_directories: frozenset[Path] = frozenset()

    def is_warm(self, key: tuple) -> bool:
        with self._condition:
            worker = self._workers.get(key)
            return bool(worker and worker.warmed and worker.process.poll() is None)

    def mark_warm(self, key: tuple) -> None:
        with self._condition:
            worker = self._workers.get(key)
            if worker is not None and worker.process.poll() is None:
                worker.warmed = True

    @contextmanager
    def acquire(
        self,
        key: tuple,
        directory: Path,
        spawn: Callable[[], subprocess.Popen],
        context: InferenceContext,
    ):
        worker = None
        waiting_worker = None
        with self._condition:
            try:
                while True:
                    context.remaining()
                    self._prune()
                    worker = self._workers.get(key)
                    if waiting_worker is not None and waiting_worker is not worker:
                        waiting_worker.waiting_queries -= 1
                        waiting_worker = None
                    if worker is None:
                        if len(self._workers) >= self.capacity:
                            idle = next(
                                (
                                    identity
                                    for identity, candidate in self._workers.items()
                                    if not candidate.busy
                                    and not candidate.waiting_queries
                                ),
                                None,
                            )
                            if idle is None:
                                raise EmbeddingError("embedding_compute_busy")
                            self._workers.pop(idle).close()
                        worker = Worker(spawn(), directory, touched=time.monotonic())
                        self._workers[key] = worker
                    if not worker.busy and (
                        context.priority == "interactive" or not worker.waiting_queries
                    ):
                        worker.busy = True
                        self._workers.move_to_end(key)
                        break
                    if context.priority == "background":
                        raise EmbeddingError("embedding_compute_busy")
                    if waiting_worker is None:
                        worker.waiting_queries += 1
                        waiting_worker = worker
                    self._condition.wait(min(0.05, context.remaining()))
            finally:
                if waiting_worker is not None:
                    waiting_worker.waiting_queries -= 1
        try:
            yield worker.process
        except BaseException:
            with self._condition:
                if self._workers.get(key) is worker:
                    self._workers.pop(key)
                worker.close()
            raise
        finally:
            with self._condition:
                worker.busy = False
                worker.touched = time.monotonic()
                self._condition.notify_all()

    def _prune(self):
        for key, worker in list(self._workers.items()):
            if (
                not worker.busy
                and not worker.waiting_queries
                and (
                    worker.process.poll() is not None
                    or (
                        worker.directory not in self._protected_directories
                        and time.monotonic() - worker.touched > self.idle_seconds
                    )
                )
            ):
                self._workers.pop(key).close()

    def prune_idle(self, protected_directories: tuple[Path, ...] = ()):
        with self._condition:
            self._protected_directories = frozenset(protected_directories[:64])
            self._prune()

    def enforce_memory_budget(
        self, process: subprocess.Popen, budget: int, rss: Callable[[int], int | None]
    ):
        """Idle cached models share the caller's native RSS allowance."""
        with self._condition:
            usages = [
                (key, worker, rss(worker.process.pid) or 0)
                for key, worker in self._workers.items()
            ]
            total = sum(size for _, _, size in usages)
            if not any(worker.process is process for _, worker, _ in usages):
                # The same budget also admits a media renderer alongside cached
                # encoders. Its RSS is external to this pool, but still counts.
                total += rss(process.pid) or 0
            for key, worker, size in usages:
                if total <= budget:
                    break
                if (
                    worker.process is not process
                    and not worker.busy
                    and not worker.waiting_queries
                ):
                    self._workers.pop(key).close()
                    total -= size
            if total > budget:
                raise EmbeddingError("embedding_worker_oom")

    def discard_directory(self, directory: Path) -> bool:
        """The disk-cache owner must evict idle children before removing files."""
        with self._condition:
            selected = [
                (key, worker)
                for key, worker in self._workers.items()
                if worker.directory == directory
            ]
            if any(worker.busy or worker.waiting_queries for _, worker in selected):
                return False
            for key, _worker in selected:
                self._workers.pop(key).close()
            return True

    def close(self):
        with self._condition:
            for worker in self._workers.values():
                worker.close()
            self._workers.clear()
            self._condition.notify_all()


pool = WorkerPool()
atexit.register(pool.close)
