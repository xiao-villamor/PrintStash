"""Transaction ownership for product operations using an existing session."""

from collections.abc import Iterator
from contextlib import contextmanager

from sqlmodel import Session


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
        connection.exec_driver_sql("BEGIN IMMEDIATE" if immediate else "BEGIN")
