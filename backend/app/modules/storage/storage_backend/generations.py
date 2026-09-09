"""Coherent catalog/adapter admission across a Vault activation.

A read pins before its first catalog lookup. Its planning lease ends when
response headers are ready; the adapter itself remains pinned until the response
ends, so an old stream need not prevent activation.
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from threading import Condition
from typing import Iterator

from .contracts import StorageBackend

_gate = Condition()
_planning = 0
_readers: dict[str, int] = {}
_activating = False
_epoch = "0"
_current: ContextVar[StorageBackend | None] = ContextVar("vault_backend", default=None)


@dataclass
class ReadGeneration:
    backend: StorageBackend
    epoch: str
    _planning: bool = True
    _closed: bool = False

    def planned(self) -> None:
        global _planning
        with _gate:
            if self._planning:
                self._planning = False
                _planning -= 1
                _gate.notify_all()

    def close(self) -> None:
        self.planned()
        with _gate:
            if not self._closed:
                _readers[self.epoch] -= 1
                if not _readers[self.epoch]:
                    del _readers[self.epoch]
                self._closed = True
                _gate.notify_all()


def has_readers(epoch: str) -> bool:
    with _gate:
        return _readers.get(epoch, 0) > 0


def current_backend() -> StorageBackend | None:
    return _current.get()


def current_epoch() -> str:
    with _gate:
        return _epoch


def pin() -> ReadGeneration:
    global _planning
    from .runtime import get_bound_backend

    with _gate:
        _gate.wait_for(lambda: not _activating)
        result = ReadGeneration(get_bound_backend(), _epoch)
        _planning += 1
        _readers[_epoch] = _readers.get(_epoch, 0) + 1
        return result


@contextmanager
def use(pinned: ReadGeneration) -> Iterator[None]:
    token = _current.set(pinned.backend)
    try:
        yield
    finally:
        _current.reset(token)
        pinned.close()


@contextmanager
def activation(*, timeout: float = 30) -> Iterator[None]:
    global _activating
    deadline = time.monotonic() + timeout
    with _gate:
        if _activating:
            raise RuntimeError("vault_activation_already_running")
        _activating = True
        while _planning:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                _activating = False
                _gate.notify_all()
                raise TimeoutError("vault_reader_drain_timeout")
            _gate.wait(remaining)
    try:
        yield
    finally:
        with _gate:
            _activating = False
            _gate.notify_all()


def publish(backend: StorageBackend, epoch: str) -> None:
    global _epoch
    from .runtime import bind_backend

    with _gate:
        if not _activating or _planning:
            raise RuntimeError("vault_activation_admission_required")
        bind_backend(backend)
        _epoch = epoch
