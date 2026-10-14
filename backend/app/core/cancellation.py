"""A scoped cooperative stop signal for synchronous supervised operations."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

# Forced checkpoints still guard every publication with a fresh read, so this
# only bounds how soon idle waiting notices a cancel.
_PROBE_SECONDS = 0.5


class OperationCancelled(BaseException):
    """Unwind owned resources without turning withdrawal into a failed result."""


@dataclass
class _Probe:
    withdrawn: Callable[[], bool]
    # What a paced checkpoint asks instead, when its owner can answer many
    # waiting operations from one read. A forced checkpoint never uses it.
    idle: Callable[[], bool] | None = None
    next_check: float = 0
    stopped: bool = False

    def check(self, *, force: bool = False) -> None:
        if not self.stopped and (force or time.monotonic() >= self.next_check):
            self.next_check = time.monotonic() + _PROBE_SECONDS
            ask = self.withdrawn if force or self.idle is None else self.idle
            self.stopped = ask()
        if self.stopped:
            raise OperationCancelled()


_current: ContextVar[_Probe | None] = ContextVar("operation_cancellation", default=None)


@contextmanager
def cancellation_scope(
    withdrawn: Callable[[], bool], *, idle: Callable[[], bool] | None = None
) -> Iterator[None]:
    token = _current.set(_Probe(withdrawn, idle))
    try:
        yield
    finally:
        _current.reset(token)


def checkpoint(*, force: bool = False) -> None:
    probe = _current.get()
    if probe is not None:
        probe.check(force=force)
