"""Optional content projections, invoked inside the content owner's transaction.

The contract has no search dependency. Bootstrap installs a projection; domain
operations publish source identities after staging their content changes. The
projection may flush but must never commit, perform inference, or publish events.
"""

from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Protocol

from sqlmodel import Session


@dataclass(frozen=True, order=True)
class ContentSource:
    kind: str
    id: int


class ContentProjection(Protocol):
    def refresh(self, session: Session, sources: tuple[ContentSource, ...]) -> None: ...


_projection: ContentProjection | None = None
_BATCH_KEY = "printstash_content_changes"


@contextmanager
def batch_content_changes(session: Session) -> Iterator[None]:
    """Project staged changes once, before returning transaction ownership.

    The owner must commit after this context exits. Nested batches share the
    outer boundary. Failures discard the queue; rollback remains the owner's job.
    """
    if _BATCH_KEY in session.info:
        yield
        return
    pending: set[ContentSource] = set()
    session.info[_BATCH_KEY] = pending
    try:
        yield
        if pending and _projection is not None:
            session.flush()
            _projection.refresh(session, tuple(sorted(pending)))
    finally:
        session.info.pop(_BATCH_KEY, None)


def bind_content_projection(
    projection: ContentProjection | None,
) -> ContentProjection | None:
    global _projection
    previous = _projection
    _projection = projection
    return previous


def content_changed(session: Session, kind: str, ids: Iterable[int | None]) -> None:
    """Project staged source edits atomically; no-op when the feature is absent."""
    if _projection is None:
        return
    # Evaluate lazy row identities after INSERT has assigned their primary keys.
    session.flush()
    sources = tuple(sorted({ContentSource(kind, id) for id in ids if id is not None}))
    if sources:
        pending = session.info.get(_BATCH_KEY)
        if pending is not None:
            pending.update(sources)
            if len(pending) >= 1024:
                _projection.refresh(session, tuple(sorted(pending)))
                pending.clear()
            return
        _projection.refresh(session, sources)
