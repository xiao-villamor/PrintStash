"""The lock order every library writer shares.

Writes to library tables advance the singleton ``library_revision`` row
through triggers, so a library write locks that row at whatever point its
first such statement runs. A transaction that also locks other rows shared
between writers (a Search projection request) must take the revision row
first; otherwise two writers that reach the two rows in opposite orders
deadlock on PostgreSQL. SQLite has one writer at a time and needs nothing.
"""

from __future__ import annotations

from sqlmodel import Session, col, select

from app.db.models import LibraryRevision


def lock_library_revision(session: Session) -> None:
    """Lock the library revision row in the caller's transaction, first."""
    if session.get_bind().dialect.name != "postgresql":
        return
    session.execute(
        select(col(LibraryRevision.id))
        .where(col(LibraryRevision.id) == 1)
        .with_for_update()
    )
