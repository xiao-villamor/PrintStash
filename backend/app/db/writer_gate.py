"""Hand the SQLite writer from one transaction to the next without polling.

SQLite admits one writer. A connection that finds the database locked waits in
SQLite's busy handler, which sleeps in growing steps (1, 2, 5 … 100 ms) and
retries. Under contention the writer sits idle while waiters sleep past its
release, and an unlucky waiter can be passed over for more than a second: an
ingestion audit measured single waits of 1.2 s while the writer was busy for a
few milliseconds at a time.

SQLite deployments run every writer in one process (``validate_topology``
refuses a split topology without PostgreSQL), so the queue can live in
memory. A connection takes the gate just before the first statement that
needs the write lock, and gives it back as soon as SQLite has ended that
transaction. A waiter then starts the moment the writer is free.

The gate only orders writers that SQLite would serialise anyway. A wait that
outlasts the busy timeout proceeds ungated, so the database's own locking,
and its ``database is locked`` error, still decide; a thread that writes on a
second connection while its first holds the gate degrades to today's
behaviour instead of deadlocking.
"""

from __future__ import annotations

import re
import sqlite3
import threading
from typing import Any

from sqlalchemy import event
from sqlalchemy.engine import Engine

from app.core.logging import get_logger

logger = get_logger(__name__)

# The statements that take SQLite's write lock. A CTE counts when it writes.
_WRITES = re.compile(
    r"^\s*(?:BEGIN\s+(?:IMMEDIATE|EXCLUSIVE)|INSERT|UPDATE|DELETE|REPLACE"
    r"|CREATE|DROP|ALTER)\b",
    re.IGNORECASE,
)
_CTE = re.compile(r"^\s*WITH\b", re.IGNORECASE)
_CTE_WRITES = re.compile(r"\b(?:INSERT|UPDATE|DELETE|REPLACE)\b", re.IGNORECASE)


def takes_write_lock(statement: str) -> bool:
    """Whether ``statement`` makes SQLite take its write lock."""
    if _WRITES.match(statement):
        return True
    return bool(_CTE.match(statement) and _CTE_WRITES.search(statement))


class WriterGate:
    """One SQLite writer at a time, handed over as soon as it commits."""

    def __init__(self, *, timeout_seconds: float) -> None:
        self._lock = threading.Lock()
        self._timeout = timeout_seconds

    def enter(self, connection: GatedConnection) -> None:
        if connection.holds_gate:
            return
        if self._lock.acquire(timeout=self._timeout):
            connection.holds_gate = True
            return
        # SQLite's own busy timeout decides from here, as it did before.
        logger.warning("sqlite writer gate wait exceeded the busy timeout")

    def leave(self, connection: GatedConnection) -> None:
        if connection.holds_gate:
            connection.holds_gate = False
            self._lock.release()


class GatedConnection(sqlite3.Connection):
    """A connection that returns the writer gate when its transaction ends."""

    gate: WriterGate | None = None

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.holds_gate = False

    def commit(self) -> None:
        try:
            super().commit()
        finally:
            self._leave()

    def rollback(self) -> None:
        try:
            super().rollback()
        finally:
            self._leave()

    def close(self) -> None:
        try:
            super().close()
        finally:
            self._leave()

    def _leave(self) -> None:
        if self.gate is not None:
            self.gate.leave(self)


def gated_connection_class(gate: WriterGate) -> type[GatedConnection]:
    """A connection class bound to ``gate``, for ``sqlite3.connect(factory=…)``."""
    return type("GatedSQLiteConnection", (GatedConnection,), {"gate": gate})


def install(engine: Engine, gate: WriterGate) -> None:
    """Take ``gate`` before each statement that needs the write lock."""

    @event.listens_for(engine, "before_cursor_execute")
    def _enter(conn, _cursor, statement, _parameters, _context, _executemany):
        dbapi = conn.connection.dbapi_connection
        if isinstance(dbapi, GatedConnection) and takes_write_lock(statement):
            gate.enter(dbapi)

    def _settle(conn, *_args: Any) -> None:
        # A write outside a transaction (autocommit) or a statement that ended
        # its transaction leaves nothing to wait for.
        dbapi = conn.connection.dbapi_connection
        if isinstance(dbapi, GatedConnection) and not dbapi.in_transaction:
            gate.leave(dbapi)

    def _settle_failed(context: Any) -> None:
        if context.connection is not None:
            _settle(context.connection)

    event.listen(engine, "after_cursor_execute", _settle)
    event.listen(engine, "handle_error", _settle_failed)
