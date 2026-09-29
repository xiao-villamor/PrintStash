"""Request-scoped SQL cursor timing without retaining SQL or parameter values."""

from __future__ import annotations

import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from threading import Lock
from typing import Iterator

from sqlalchemy import event
from sqlalchemy.engine import Engine


@dataclass
class SqlRequestStats:
    statement_count: int = 0
    duration_ms: float = 0.0
    _lock: Lock = field(default_factory=Lock, repr=False)

    def add_statement(self) -> None:
        with self._lock:
            self.statement_count += 1

    def add_duration(self, elapsed_ms: float) -> None:
        with self._lock:
            self.duration_ms += elapsed_ms


_active_stats: ContextVar[SqlRequestStats | None] = ContextVar(
    "request_sql_stats", default=None
)


@contextmanager
def capture_sql_timing() -> Iterator[SqlRequestStats]:
    stats = SqlRequestStats()
    token = _active_stats.set(stats)
    try:
        yield stats
    finally:
        _active_stats.reset(token)


@event.listens_for(Engine, "before_cursor_execute")
def _before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
    stats = _active_stats.get()
    if stats is None:
        return
    stats.add_statement()
    context._printstash_request_sql_timing = (stats, time.perf_counter())


def _finish_cursor(context) -> None:
    active = getattr(context, "_printstash_request_sql_timing", None)
    if active is None:
        return
    context._printstash_request_sql_timing = None
    stats, started = active
    stats.add_duration((time.perf_counter() - started) * 1000)


@event.listens_for(Engine, "after_cursor_execute")
def _after_cursor_execute(conn, cursor, statement, parameters, context, executemany):
    _finish_cursor(context)


@event.listens_for(Engine, "handle_error")
def _handle_error(exception_context):
    if exception_context.execution_context is not None:
        _finish_cursor(exception_context.execution_context)
