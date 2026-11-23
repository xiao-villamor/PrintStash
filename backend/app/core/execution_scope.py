"""Which Job execution the current code runs for, for logs and metrics.

The Job runner binds the identity around each step. Code that runs outside a
Job (a request, startup, a test) has none, and says so: telemetry records
``None`` rather than guessing. This owner has no ORM or engine dependencies,
so native supervision and database helpers can read it.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass


@dataclass(frozen=True)
class ExecutionIdentity:
    job_id: str
    execution_id: str
    definition: str


_current: ContextVar[ExecutionIdentity | None] = ContextVar(
    "execution_identity", default=None
)


def current_execution() -> ExecutionIdentity | None:
    """The Job execution this code runs for; ``None`` outside a Job step."""
    return _current.get()


@contextmanager
def execution_scope(identity: ExecutionIdentity) -> Iterator[None]:
    token = _current.set(identity)
    try:
        yield
    finally:
        _current.reset(token)
