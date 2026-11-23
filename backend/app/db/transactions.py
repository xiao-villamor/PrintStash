"""Transaction ownership for product operations using an existing session."""

import time
from collections.abc import Iterator
from contextlib import contextmanager

from sqlmodel import Session

from app.core.execution_scope import current_execution
from app.core.logging import get_logger

logger = get_logger(__name__)

# A writer wait this long is worth a log line naming who waited.
_SLOW_WRITER_WAIT_SECONDS = 0.25
# The metric label for writes made outside any Job (requests, startup).
_OUTSIDE_JOBS = "none"


@contextmanager
def rollback_on_failure(session: Session) -> Iterator[None]:
    """The operation commits; any failed exit rolls back its pending SQL.

    This deliberately does not begin a nested transaction or commit on exit.
    The caller may already have opened a transaction while resolving authority.
    No storage compensation is implied by a database rollback.
    """
    try:
        yield
    except BaseException:
        session.rollback()
        raise


def begin_write(session: Session, *, immediate: bool = False) -> None:
    # sqlite3's legacy mode starts transactions for ordinary DML, but not for
    # WITH ... INSERT or SAVEPOINT. SQLAlchemy's logical transaction alone does
    # not protect those writes from committing before a caller rollback.
    connection = session.connection()
    if (
        connection.dialect.name == "sqlite"
        and not connection.connection.driver_connection.in_transaction
    ):
        # Read-dependent mutations can reserve the writer before taking a WAL
        # snapshot, avoiding an impossible read-to-write upgrade after a commit
        # on another connection. Existing caller transactions remain untouched.
        if not immediate:
            connection.exec_driver_sql("BEGIN")
            return
        started = time.monotonic()
        connection.exec_driver_sql("BEGIN IMMEDIATE")
        _record_writer_wait(time.monotonic() - started)


def _record_writer_wait(seconds: float) -> None:
    from app.core.metrics import record_writer_wait

    execution = current_execution()
    record_writer_wait(
        execution.definition if execution is not None else _OUTSIDE_JOBS, seconds
    )
    if seconds >= _SLOW_WRITER_WAIT_SECONDS:
        logger.info(
            "sqlite_writer_wait seconds=%.3f job_id=%s execution_id=%s definition=%s",
            seconds,
            execution.job_id if execution is not None else None,
            execution.execution_id if execution is not None else None,
            execution.definition if execution is not None else None,
        )
