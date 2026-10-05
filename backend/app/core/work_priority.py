"""Closed scheduling priorities shared by Jobs and physical resource admission.

This owner has no ORM or engine dependencies: worker guardians can validate
resource receipts without importing the application database.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from enum import StrEnum


class WorkPriority(StrEnum):
    """Scheduling tier of a submission; children inherit it and may only lower it."""

    INTERACTIVE = "interactive"
    BACKFILL = "backfill"


_current: ContextVar[WorkPriority] = ContextVar(
    "work_priority", default=WorkPriority.INTERACTIVE
)


def current_priority() -> WorkPriority:
    """Outside a Job, synchronous callers represent work a user is waiting for."""
    return _current.get()


@contextmanager
def priority_scope(priority: WorkPriority) -> Iterator[None]:
    if not isinstance(priority, WorkPriority):
        raise TypeError("priority must be a WorkPriority")
    token = _current.set(priority)
    try:
        yield
    finally:
        _current.reset(token)
