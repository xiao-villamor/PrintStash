"""Durable source notifications and bounded, transactional projection pages.

A content writer records one compact source identity. All dependency fanout,
passage construction and lexical maintenance happen in the background. Work and
its cursor commit together; a crash can replay a page without losing a change.
"""

from collections.abc import Iterator
from datetime import timedelta

from printstash_core.search.passages import SearchSubject, SubjectType
from sqlalchemy import and_, literal, or_, union, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlmodel import Session, col, select

from app.core.logging import get_logger
from app.core.time import utcnow
from app.db.models import (
    Collection,
    Document,
    File,
    Model,
    ModelProvenanceSource,
    MultipartModel,
    MultipartModelChoice,
    SearchDependency,
    SearchProjectionRequest,
)
from app.db.projections import ContentSource
from app.modules.library.library_search import effective_tag_pairs
from app.modules.search.passages import sync_subject

logger = get_logger(__name__)
PAGE_SIZE = 128


def _affected_query(session: Session, source: ContentSource):
    statements = [
        select(
            SearchDependency.subject_type.label("kind"),
            SearchDependency.subject_id.label("subject_id"),
        ).where(
            SearchDependency.source_kind == source.kind,
            SearchDependency.source_id == source.id,
        )
    ]
    if source.kind in {kind.value for kind in SubjectType}:
        statements.append(select(literal(source.kind), literal(source.id)))
    if source.kind == "model":
        statements.append(
            select(
                literal("multipart_model"), MultipartModelChoice.multipart_model_id
            ).where(MultipartModelChoice.model_id == source.id)
        )
    elif source.kind == "collection":
        collection = session.get(Collection, source.id)
        if collection is not None:
            prefix = (
                collection.path.replace("\\", "\\\\")
                .replace("%", "\\%")
                .replace("_", "\\_")
            )
            descendants = select(Collection.id).where(
                or_(
                    Collection.id == source.id,
                    col(Collection.path).like(prefix + "/%", escape="\\"),
                )
            )
            statements.append(
                select(literal("collection"), Collection.id).where(
                    col(Collection.id).in_(descendants)
                )
            )
            for kind, table in (
                ("model", Model),
                ("document", Document),
                ("multipart_model", MultipartModel),
            ):
                statements.append(
                    select(literal(kind), table.id).where(
                        col(table.collection_id).in_(descendants)
                    )
                )
    elif source.kind == "file":
        statements.append(
            select(literal("model"), File.model_id).where(File.id == source.id)
        )
    elif source.kind == "provenance":
        statements.append(
            select(literal("model"), ModelProvenanceSource.model_id).where(
                ModelProvenanceSource.id == source.id
            )
        )
    elif source.kind == "tag":
        pairs = effective_tag_pairs().subquery()
        statements.append(
            select(literal("model"), pairs.c.model_id).where(
                pairs.c.tag_id == source.id
            )
        )
    return union(*statements).subquery()


def _page(session: Session, source: ContentSource, after: tuple[str, int], limit: int):
    subjects = _affected_query(session, source)
    return session.execute(
        select(subjects.c.kind, subjects.c.subject_id)
        .where(
            or_(
                subjects.c.kind > after[0],
                and_(subjects.c.kind == after[0], subjects.c.subject_id > after[1]),
            )
        )
        .order_by(subjects.c.kind, subjects.c.subject_id)
        .limit(limit)
    ).all()


def affected_subjects(
    session: Session, source: ContentSource
) -> Iterator[SearchSubject]:
    """Keyset the union, including dependencies for relationships now removed."""
    after = ("", 0)
    while rows := _page(session, source, after, PAGE_SIZE):
        for kind, id in rows:
            yield SearchSubject(SubjectType(kind), id)
        after = tuple(rows[-1])


class LibraryProjection:
    def refresh(self, session: Session, sources: tuple[ContentSource, ...]) -> None:
        """Register changes in the caller's transaction; never render or commit."""
        insert = (
            pg_insert
            if session.get_bind().dialect.name == "postgresql"
            else sqlite_insert
        )
        now = utcnow()
        for source in sources:
            statement = insert(SearchProjectionRequest).values(
                source_kind=source.kind,
                source_id=source.id,
                revision=1,
                cursor_kind="",
                cursor_id=0,
                attempts=0,
                next_attempt_at=now,
                created_at=now,
                updated_at=now,
            )
            session.execute(
                statement.on_conflict_do_update(
                    index_elements=["source_kind", "source_id"],
                    set_={
                        "revision": SearchProjectionRequest.revision + 1,
                        "cursor_kind": "",
                        "cursor_id": 0,
                        "attempts": 0,
                        "next_attempt_at": now,
                        "error_code": None,
                        "updated_at": now,
                    },
                )
            )


def process_pending(session: Session, *, limit: int = 8) -> int:
    """Stage at most one page; caller commits the page and durable checkpoint.

    The short write reservation serializes a page with source notifications on
    SQLite and PostgreSQL. Expensive inference never executes here. A failed
    page is rolled back independently and delayed, leaving other sources eligible.
    """
    if not 1 <= limit <= PAGE_SIZE:
        raise ValueError("search_projection_limit")
    request = session.exec(
        select(SearchProjectionRequest)
        .where(SearchProjectionRequest.next_attempt_at <= utcnow())
        .order_by(SearchProjectionRequest.next_attempt_at, SearchProjectionRequest.id)
        .limit(1)
        .with_for_update(skip_locked=True)
    ).first()
    if request is None:
        return 0
    claimed = session.connection().execute(
        update(SearchProjectionRequest)
        .where(
            SearchProjectionRequest.id == request.id,
            SearchProjectionRequest.revision == request.revision,
        )
        .values(revision=SearchProjectionRequest.revision)
    )
    if claimed.rowcount != 1:
        return 0
    session.refresh(request)
    try:
        with session.begin_nested():
            rows = _page(
                session,
                ContentSource(request.source_kind, request.source_id),
                (request.cursor_kind, request.cursor_id),
                limit,
            )
            for kind, id in rows:
                sync_subject(session, SearchSubject(SubjectType(kind), id))
            if len(rows) < limit:
                session.delete(request)
            else:
                request.cursor_kind, request.cursor_id = rows[-1]
                # Rotate large collections behind other waiting source changes.
                request.next_attempt_at = utcnow()
                request.updated_at = utcnow()
                session.add(request)
            session.flush()
    except Exception:
        logger.exception(
            "Search projection page deferred",
            extra={"source_kind": request.source_kind, "source_id": request.source_id},
        )
        request.attempts += 1
        request.next_attempt_at = utcnow() + timedelta(
            seconds=min(2 ** min(request.attempts, 8), 300)
        )
        request.error_code = "projection_failed"
        session.add(request)
        session.flush()
        return 0
    return max(len(rows), 1)
