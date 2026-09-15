"""Synchronous projection adapter; library owners depend only on the neutral port."""

from collections.abc import Iterator

from printstash_core.search.passages import SearchSubject, SubjectType
from sqlalchemy import or_
from sqlmodel import Session, select

from app.db.models import (
    Collection,
    Document,
    File,
    Model,
    ModelProvenanceSource,
    MultipartModel,
    MultipartModelChoice,
    SearchDependency,
)
from app.db.projections import ContentSource
from app.modules.library.library_search import effective_tag_pairs
from app.modules.search.passages import sync_subject

PAGE_SIZE = 128


def _subjects(
    session: Session, kind: SubjectType, statement
) -> Iterator[SearchSubject]:
    """Keyset a source query without retaining a whole Collection in memory."""
    cursor = 0
    while True:
        id_column = statement.selected_columns[0]
        ids = session.exec(
            statement.where(id_column > cursor).order_by(id_column).limit(PAGE_SIZE)
        ).all()
        for id in ids:
            yield SearchSubject(kind, id)
        if len(ids) < PAGE_SIZE:
            return
        cursor = ids[-1]


def affected_subjects(
    session: Session, source: ContentSource
) -> Iterator[SearchSubject]:
    # The old dependency inventory catches a removed relationship/source even
    # when current joins can no longer reach its former owner.
    statement = select(SearchDependency).where(
        SearchDependency.source_kind == source.kind,
        SearchDependency.source_id == source.id,
    )
    cursor = 0
    while True:
        rows = session.exec(
            statement.where(SearchDependency.id > cursor)
            .order_by(SearchDependency.id)
            .limit(PAGE_SIZE)
        ).all()
        for row in rows:
            yield SearchSubject(SubjectType(row.subject_type), row.subject_id)
        if len(rows) < PAGE_SIZE:
            break
        cursor = rows[-1].id
    if source.kind in {kind.value for kind in SubjectType}:
        yield SearchSubject(SubjectType(source.kind), source.id)
    if source.kind == "model":
        yield from _subjects(
            session,
            SubjectType.MULTIPART_MODEL,
            select(MultipartModelChoice.multipart_model_id)
            .where(MultipartModelChoice.model_id == source.id)
            .distinct(),
        )
    elif source.kind == "collection":
        collection = session.get(Collection, source.id)
        if collection is None:
            return
        # Escaped path prefix, with a separator: sibling slugs cannot match.
        prefix = (
            collection.path.replace("\\", "\\\\")
            .replace("%", "\\%")
            .replace("_", "\\_")
        )
        descendants = select(Collection.id).where(
            or_(
                Collection.id == source.id,
                Collection.path.like(prefix + "/%", escape="\\"),
            )
        )
        yield from _subjects(session, SubjectType.COLLECTION, descendants)
        for kind, table in (
            (SubjectType.MODEL, Model),
            (SubjectType.DOCUMENT, Document),
            (SubjectType.MULTIPART_MODEL, MultipartModel),
        ):
            yield from _subjects(
                session,
                kind,
                select(table.id).where(table.collection_id.in_(descendants)),
            )
    elif source.kind == "file":
        row = session.get(File, source.id)
        if row is not None:
            yield SearchSubject(SubjectType.MODEL, row.model_id)
    elif source.kind == "provenance":
        row = session.get(ModelProvenanceSource, source.id)
        if row is not None:
            yield SearchSubject(SubjectType.MODEL, row.model_id)
    elif source.kind == "tag":
        pairs = effective_tag_pairs().subquery()
        yield from _subjects(
            session,
            SubjectType.MODEL,
            select(pairs.c.model_id).where(pairs.c.tag_id == source.id).distinct(),
        )


class LibraryProjection:
    def refresh(self, session: Session, sources: tuple[ContentSource, ...]) -> None:
        recent: set[SearchSubject] = set()
        for source in sources:
            # Duplicates are harmless and idempotent. A bounded recent set avoids
            # repeated joins without allocating a library-sized deduplication set.
            for subject in affected_subjects(session, source):
                if subject not in recent:
                    sync_subject(session, subject)
                    recent.add(subject)
                if len(recent) >= PAGE_SIZE:
                    recent.clear()
