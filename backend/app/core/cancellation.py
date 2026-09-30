"""A scoped cooperative stop signal for synchronous supervised operations."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass


class OperationCancelled(BaseException):
    """Unwind owned resources without turning withdrawal into a failed result."""


@dataclass
class _Probe:
    withdrawn: Callable[[], bool]
    next_check: float = 0
    stopped: bool = False

    def check(self, *, force: bool = False) -> None:
        if not self.stopped and (force or time.monotonic() >= self.next_check):
            self.next_check = time.monotonic() + 0.2
            self.stopped = self.withdrawn()
        if self.stopped:
            raise OperationCancelled()


_current: ContextVar[_Probe | None] = ContextVar("operation_cancellation", default=None)


@contextmanager
def cancellation_scope(withdrawn: Callable[[], bool]) -> Iterator[None]:
    token = _current.set(_Probe(withdrawn))
    try:
        yield
    finally:
        _current.reset(token)


def checkpoint(*, force: bool = False) -> None:
    probe = _current.get()
    if probe is not None:
        probe.check(force=force)
