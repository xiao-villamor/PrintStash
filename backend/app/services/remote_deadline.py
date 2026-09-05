"""A synchronous call-context budget passed down into native transport timeouts."""

from __future__ import annotations

import asyncio
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from threading import Event

from app.services.storage_backend import StorageConfigurationError


@dataclass(frozen=True)
class _Budget:
    deadline: float | None
    cancelled: Event | None


_BUDGET: ContextVar[_Budget] = ContextVar(
    "remote_io_budget",
    default=_Budget(None, None),  # noqa: B039 - frozen value
)


@contextmanager
def remote_budget(*, deadline: float | None = None, cancelled: Event | None = None):
    previous = _BUDGET.get()
    deadlines = [value for value in (deadline, previous.deadline) if value is not None]
    token = _BUDGET.set(
        _Budget(min(deadlines) if deadlines else None, cancelled or previous.cancelled)
    )
    try:
        yield
    finally:
        _BUDGET.reset(token)


def operation_timeout() -> float:
    budget = _BUDGET.get()
    if budget.cancelled is not None and budget.cancelled.is_set():
        raise asyncio.CancelledError()
    remaining = 60.0 if budget.deadline is None else budget.deadline - time.monotonic()
    if remaining <= 0:
        raise StorageConfigurationError("remote_scan_slice_deadline")
    return min(60.0, remaining)


def paced_sleep(seconds: float) -> None:
    end = time.monotonic() + max(0, seconds)
    while (remaining := end - time.monotonic()) > 0:
        pause = min(remaining, operation_timeout(), 0.1)
        budget = _BUDGET.get()
        if budget.cancelled is not None:
            budget.cancelled.wait(pause)
        else:
            time.sleep(pause)
    operation_timeout()
