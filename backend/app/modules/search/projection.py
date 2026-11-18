"""Durable source notifications and bounded, transactional projection pages.

A content writer records one compact source identity. All dependency fanout,
passage construction and lexical maintenance happen in the background. Work and
its cursor commit together; a crash can replay a page without losing a change.
"""

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import timedelta

from printstash_core.search.passages import SearchSubject, SubjectType
from sqlalchemy import and_, literal, or_, union, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlmodel import Session, col, select

from app.core.logging import get_logger
from app.core.time import utcnow
from app.db.library_revision import lock_library_revision
from app.db.models import (
    Collection,
    Document,
    File,
    JobKind,
    Model,
    ModelProvenanceSource,
    MultipartModel,
    MultipartModelChoice,
    SearchDependency,
    SearchProjectionRequest,
)
from app.db.projections import ContentSource
from app.db.transactions import begin_write
from app.modules.library.library_search import effective_tag_pairs
from app.modules.search.passages import (
    PreparedSubject,
    passage_policy,
    prepare_subjects,
    sync_subject,
)

logger = get_logger(__name__)
PAGE_SIZE = 128
SOURCE_BATCH_SIZE = 8


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
        # A request row is locked after the library revision row, never before.
        lock_library_revision(session)
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
        if sources:
            from app.modules.work.submission import nudge_after_commit

            # The request row is the intent; the nudge only makes it prompt.
            nudge_after_commit(session, JobKind.SEARCH_PROJECT)


@dataclass(frozen=True)
class PreparedRequest:
    """A bounded prefix guarded by its source revision and cursor."""

    request_id: int
    revision: int
    cursor: tuple[str, int]
    subjects: tuple[SearchSubject, ...]
    exhausted: bool


@dataclass(frozen=True)
class PreparedPage:
    """Source prefixes sharing bounded preparation, with independent recovery."""

    requests: tuple[PreparedRequest, ...]
    subjects: tuple[tuple[SearchSubject, PreparedSubject], ...]


def prepare_pending(session: Session, *, limit: int = 8) -> PreparedPage | None:
    """Read at most limit distinct Subjects, outside the writer transaction."""
    if not 1 <= limit <= PAGE_SIZE:
        raise ValueError("search_projection_limit")
    requests = session.exec(
        select(SearchProjectionRequest)
        .where(SearchProjectionRequest.next_attempt_at <= utcnow())
        .order_by(SearchProjectionRequest.next_attempt_at, SearchProjectionRequest.id)
        .limit(SOURCE_BATCH_SIZE)
    ).all()
    if not requests:
        return None
    pages: list[PreparedRequest] = []
    subjects: list[SearchSubject] = []
    seen: set[SearchSubject] = set()
    for request in requests:
        if request.id is None:
            raise RuntimeError("search_projection_request_missing")
        cursor = (request.cursor_kind, request.cursor_id)
        rows = _page(
            session,
            ContentSource(request.source_kind, request.source_id),
            cursor,
            limit,
        )
        prefix: list[SearchSubject] = []
        for kind, id in rows:
            subject = SearchSubject(SubjectType(kind), id)
            if subject not in seen:
                if len(subjects) == limit:
                    break
                subjects.append(subject)
                seen.add(subject)
            prefix.append(subject)
        if rows and not prefix:
            # Never advance past an uncovered Subject. Other sources may still
            # share the Subjects already in this bounded preparation.
            continue
        pages.append(
            PreparedRequest(
                request.id,
                request.revision,
                cursor,
                tuple(prefix),
                len(rows) < limit and len(prefix) == len(rows),
            )
        )
    return PreparedPage(
        tuple(pages),
        prepare_subjects(session, tuple(subjects)),
    )


def apply_prepared(session: Session, page: PreparedPage) -> int | None:
    """Fence every source before publication; recover each source independently.

    A stale batch returns None so the caller prepares fresh content. A later
    change outside this batch remains owed through its own durable request.
    """
    begin_write(session, immediate=True)
    # Preserve the lock order shared by all PostgreSQL library writers.
    lock_library_revision(session)
    requests: list[tuple[PreparedRequest, SearchProjectionRequest]] = []
    for source in page.requests:
        claimed = session.connection().execute(
            update(SearchProjectionRequest)
            .where(
                SearchProjectionRequest.id == source.request_id,
                SearchProjectionRequest.revision == source.revision,
                SearchProjectionRequest.cursor_kind == source.cursor[0],
                SearchProjectionRequest.cursor_id == source.cursor[1],
            )
            .values(revision=SearchProjectionRequest.revision)
        )
        if claimed.rowcount != 1:
            return None
        request = session.get(
            SearchProjectionRequest, source.request_id, populate_existing=True
        )
        if request is None:
            raise RuntimeError("search_projection_request_missing")
        requests.append((source, request))

    prepared = dict(page.subjects)
    applied: set[SearchSubject] = set()
    completed = False
    policy = None
    for source, request in requests:
        published: set[SearchSubject] = set()
        try:
            with session.begin_nested():
                for subject in source.subjects:
                    if subject not in applied:
                        current = prepared[subject]
                        if policy is None and current.projection is not None:
                            policy = passage_policy(session)
                        sync_subject(session, subject, prepared=current, policy=policy)
                        published.add(subject)
                if source.exhausted:
                    session.delete(request)
                else:
                    last = source.subjects[-1]
                    request.cursor_kind = last.subject_type.value
                    request.cursor_id = last.subject_id
                    request.next_attempt_at = utcnow()
                    request.updated_at = utcnow()
                    session.add(request)
                session.flush()
        except Exception:
            logger.exception(
                "Search projection page deferred",
                extra={
                    "source_kind": request.source_kind,
                    "source_id": request.source_id,
                },
            )
            request.attempts += 1
            request.next_attempt_at = utcnow() + timedelta(
                seconds=min(2 ** min(request.attempts, 8), 300)
            )
            request.error_code = "projection_failed"
            session.add(request)
            session.flush()
            continue
        # Rolled-back work cannot satisfy another source's overlapping prefix.
        applied.update(published)
        completed = True
    return max(len(applied), 1) if completed else 0


def process_pending(session: Session, *, limit: int = 8) -> int:
    """Stage at most one page; caller commits the page and durable checkpoint.

    Reads and writes in the caller's one transaction, for a caller that already
    holds the writer or does not care how long it is held. The projection Job
    prepares on a read snapshot instead and applies in a short transaction.
    Expensive inference never executes here.
    """
    page = prepare_pending(session, limit=limit)
    if page is None:
        return 0
    return apply_prepared(session, page) or 0
