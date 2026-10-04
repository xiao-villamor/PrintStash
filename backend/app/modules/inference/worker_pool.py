"""Bounded reuse of monitored ONNX children; no queries or vectors live here."""

from __future__ import annotations

import atexit
import subprocess
import threading
import time
from collections import OrderedDict
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from printstash_core.inference import EmbeddingError
from printstash_core.inference.context import InferenceContext

from app.modules.media.worker_bootstrap import (
    WorkerLifecycle,
    reap_descendants,
    terminate_worker,
)


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
            terminate_worker(self.process, WorkerLifecycle.GUARDED)
        self.process.wait()
        reap_descendants(self.process.pid)
        for stream in (self.process.stdin, self.process.stdout):
            if stream is not None:
                stream.close()


@dataclass(frozen=True)
class _PendingSpawn:
    directory: Path
    generation: int
    token: object = field(default_factory=object)


class WorkerPool:
    def __init__(self, capacity: int = 2, idle_seconds: float = 300):
        if not 1 <= capacity <= 2 or not 1 <= idle_seconds <= 3600:
            raise ValueError("embedding_worker_cache_invalid")
        self.capacity, self.idle_seconds = capacity, idle_seconds
        self._condition = threading.Condition()
        self._workers: OrderedDict[tuple, Worker] = OrderedDict()
        self._pending: dict[tuple, _PendingSpawn] = {}
        self._retiring: dict[int, Worker] = {}
        self._closing: set[int] = set()
        self._generation = 0
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

    @staticmethod
    def _remaining(context: InferenceContext, deadline: float | None) -> float:
        remaining = context.remaining()
        if deadline is not None:
            remaining = min(remaining, deadline - time.monotonic())
            if remaining <= 0:
                raise EmbeddingError("embedding_timeout")
        return remaining

    def _retire_locked(self, worker: Worker) -> bool:
        """Keep capacity until cleanup succeeds; grant one active cleanup owner."""
        identity = id(worker)
        if identity in self._closing:
            return False
        self._retiring[identity] = worker
        self._closing.add(identity)
        return True

    def _close_retired(self, workers: list[Worker]) -> None:
        first_error: BaseException | None = None
        for worker in workers:
            succeeded = False
            try:
                worker.close()
                succeeded = True
            except BaseException as error:
                if first_error is None:
                    first_error = error
            finally:
                with self._condition:
                    if succeeded and self._retiring.get(id(worker)) is worker:
                        self._retiring.pop(id(worker))
                    self._closing.remove(id(worker))
                    self._condition.notify_all()
        if first_error is not None:
            raise first_error

    def _prune_locked(self, now: float) -> list[Worker]:
        retired = []
        for key, worker in list(self._workers.items()):
            if (
                not worker.busy
                and not worker.waiting_queries
                and (
                    worker.process.poll() is not None
                    or (
                        worker.directory not in self._protected_directories
                        and now - worker.touched > self.idle_seconds
                    )
                )
            ):
                self._workers.pop(key)
                if self._retire_locked(worker):
                    retired.append(worker)
        return retired

    def _spawn_pending(
        self,
        key: tuple,
        pending: _PendingSpawn,
        spawn: Callable[[], subprocess.Popen],
        context: InferenceContext,
        deadline: float | None,
    ) -> Worker:
        candidate = None
        published = False
        retired = []
        try:
            candidate = Worker(spawn(), pending.directory, touched=time.monotonic())
            self._remaining(context, deadline)
            with self._condition:
                current = self._pending.get(key)
                if (
                    current is None
                    or current.token is not pending.token
                    or pending.generation != self._generation
                ):
                    raise EmbeddingError("embedding_compute_busy")
                self._pending.pop(key)
                candidate.busy = True
                self._workers[key] = candidate
                published = True
                self._condition.notify_all()
            return candidate
        finally:
            if not published:
                with self._condition:
                    current = self._pending.get(key)
                    if current is not None and current.token is pending.token:
                        self._pending.pop(key)
                    if candidate is not None and self._retire_locked(candidate):
                        retired.append(candidate)
                    self._condition.notify_all()
                self._close_retired(retired)

    def _claim(
        self,
        key: tuple,
        directory: Path,
        spawn: Callable[[], subprocess.Popen],
        context: InferenceContext,
        deadline: float | None,
    ) -> Worker:
        waiting_worker = None
        with self._condition:
            generation = self._generation
        try:
            while True:
                remaining = self._remaining(context, deadline)
                now = time.monotonic()
                pending = None
                with self._condition:
                    if generation != self._generation:
                        raise EmbeddingError("embedding_compute_busy")
                    retired = self._prune_locked(now)
                    worker = self._workers.get(key)
                    if waiting_worker is not None and waiting_worker is not worker:
                        waiting_worker.waiting_queries -= 1
                        waiting_worker = None
                    if not retired:
                        if worker is None and key not in self._pending:
                            occupied = (
                                len(self._workers)
                                + len(self._pending)
                                + len(self._retiring)
                            )
                            if occupied >= self.capacity:
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
                                evicted = self._workers.pop(idle)
                                if self._retire_locked(evicted):
                                    retired.append(evicted)
                            else:
                                pending = _PendingSpawn(directory, generation)
                                self._pending[key] = pending
                        if not retired and pending is None:
                            if (
                                worker is not None
                                and not worker.busy
                                and (
                                    context.priority == "interactive"
                                    or not worker.waiting_queries
                                )
                            ):
                                worker.busy = True
                                self._workers.move_to_end(key)
                                return worker
                            if context.priority == "background":
                                raise EmbeddingError("embedding_compute_busy")
                            if worker is not None and waiting_worker is None:
                                worker.waiting_queries += 1
                                waiting_worker = worker
                            self._condition.wait(min(0.05, remaining))
                self._close_retired(retired)
                if pending is not None:
                    return self._spawn_pending(key, pending, spawn, context, deadline)
        finally:
            if waiting_worker is not None:
                with self._condition:
                    waiting_worker.waiting_queries -= 1
                    self._condition.notify_all()

    @contextmanager
    def acquire(
        self,
        key: tuple,
        directory: Path,
        spawn: Callable[[], subprocess.Popen],
        context: InferenceContext,
        *,
        deadline: float | None = None,
    ):
        worker = self._claim(key, directory, spawn, context, deadline)
        try:
            yield worker.process
        except BaseException:
            retired = []
            with self._condition:
                if self._workers.get(key) is worker:
                    self._workers.pop(key)
                if self._retire_locked(worker):
                    retired.append(worker)
                self._condition.notify_all()
            self._close_retired(retired)
            raise
        finally:
            now = time.monotonic()
            with self._condition:
                worker.busy = False
                worker.touched = now
                self._condition.notify_all()

    def prune_idle(self, protected_directories: tuple[Path, ...] = ()):
        now = time.monotonic()
        with self._condition:
            self._protected_directories = frozenset(protected_directories[:64])
            retired = [
                worker
                for worker in self._retiring.values()
                if self._retire_locked(worker)
            ]
            retired.extend(self._prune_locked(now))
        self._close_retired(retired)

    def discard_directory(self, directory: Path) -> bool:
        """The disk-cache owner must evict idle children before removing files."""
        with self._condition:
            if any(
                pending.directory == directory for pending in self._pending.values()
            ) or any(
                worker.directory == directory for worker in self._retiring.values()
            ):
                return False
            selected = [
                (key, worker)
                for key, worker in self._workers.items()
                if worker.directory == directory
            ]
            if any(worker.busy or worker.waiting_queries for _, worker in selected):
                return False
            retired = []
            for key, worker in selected:
                self._workers.pop(key)
                if self._retire_locked(worker):
                    retired.append(worker)
        self._close_retired(retired)
        return True

    def close(self):
        with self._condition:
            self._generation += 1
            selected = list(self._retiring.values()) + list(self._workers.values())
            retired = [worker for worker in selected if self._retire_locked(worker)]
            self._workers.clear()
            self._condition.notify_all()
        self._close_retired(retired)


pool = WorkerPool()
atexit.register(pool.close)
