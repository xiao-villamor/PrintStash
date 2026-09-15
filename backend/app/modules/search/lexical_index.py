"""Transactional lexical statistics and optional SQLite FTS5 materialization.

Passages remain usable through ranked LIKE if the native index cannot be read
or updated. Native repair works in bounded keyset pages in the background.
"""

from __future__ import annotations

from dataclasses import dataclass

from printstash_core.search.lexical import frequencies
from sqlalchemy import delete, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import aliased
from sqlmodel import Session, select

from app.db.derived_objects import SEARCH_FTS as FTS_NAME
from app.db.models.search import (
    SearchLexicalPosting,
    SearchLexicalState,
    SearchLexicalTerm,
    SearchPassage,
)


@dataclass(frozen=True)
class LexicalFields:
    title: str
    tags: str
    text: str
    token_count: int


def snapshot(row: SearchPassage) -> LexicalFields:
    return LexicalFields(row.title, row.tags_text, row.text, row.token_count)


def state(session: Session) -> SearchLexicalState:
    dialect = session.get_bind().dialect.name
    insert = sqlite_insert if dialect == "sqlite" else pg_insert
    session.exec(
        insert(SearchLexicalState)
        .values(
            id=1,
            document_count=0,
            total_length=0,
            native_phase="absent",
            native_after_id=0,
        )
        .on_conflict_do_nothing(index_elements=["id"])
    )
    return session.exec(
        select(SearchLexicalState).where(SearchLexicalState.id == 1)
    ).one()


def _mark_broken(session: Session) -> None:
    current = state(session)
    current.native_phase = "broken"
    current.failure_code = "search_fts_unavailable"
    session.add(current)
    session.flush()


def _fts_exists(session: Session) -> bool:
    return (
        session.execute(
            text("SELECT 1 FROM sqlite_master WHERE type='table' AND name=:name"),
            {"name": FTS_NAME},
        ).first()
        is not None
    )


def _fts_replace(
    session: Session, id: int, old: LexicalFields | None, new: LexicalFields | None
) -> None:
    # docsize is an exact shadow belonging to our registered FTS table. It
    # distinguishes indexed rows from rows only present in external content
    # while the bounded initial build is still in progress.
    present = session.execute(
        text("SELECT 1 FROM search_passages_fts_docsize WHERE id=:id"), {"id": id}
    ).first()
    if present is not None and old is not None:
        session.execute(
            text(
                "INSERT INTO search_passages_fts(search_passages_fts,rowid,title,tags_text,text) VALUES ('delete',:id,:title,:tags,:text)"
            ),
            {"id": id, "title": old.title, "tags": old.tags, "text": old.text},
        )
    if new is not None:
        session.execute(
            text(
                "INSERT INTO search_passages_fts(rowid,title,tags_text,text) VALUES (:id,:title,:tags,:text)"
            ),
            {"id": id, "title": new.title, "tags": new.tags, "text": new.text},
        )


def replace(
    session: Session,
    row: SearchPassage,
    old: LexicalFields | None,
    *,
    deleted: bool = False,
) -> None:
    """Stage lexical changes; an optional native failure never rolls back content."""
    assert row.id is not None
    length, terms = (
        (0, {}) if deleted else frequencies(row.title, row.tags_text, row.text)
    )
    previous = dict(
        session.exec(
            select(SearchLexicalPosting.term, SearchLexicalPosting.frequency).where(
                SearchLexicalPosting.passage_id == row.id
            )
        ).all()
    )
    current = state(session)
    old_length = old.token_count if old is not None else 0
    if terms != previous or length != old_length:
        insert = (
            sqlite_insert if session.get_bind().dialect.name == "sqlite" else pg_insert
        )
        for term in previous.keys() | terms.keys():
            delta = int(term in terms) - int(term in previous)
            if delta > 0:
                statement = insert(SearchLexicalTerm).values(
                    term=term, document_frequency=1
                )
                session.exec(
                    statement.on_conflict_do_update(
                        index_elements=["term"],
                        set_={
                            "document_frequency": SearchLexicalTerm.document_frequency
                            + 1
                        },
                    )
                )
            elif delta < 0:
                session.exec(
                    update(SearchLexicalTerm)
                    .where(SearchLexicalTerm.term == term)
                    .values(document_frequency=SearchLexicalTerm.document_frequency - 1)
                )
                session.exec(
                    delete(SearchLexicalTerm).where(
                        SearchLexicalTerm.term == term,
                        SearchLexicalTerm.document_frequency <= 0,
                    )
                )
        session.exec(
            delete(SearchLexicalPosting).where(
                SearchLexicalPosting.passage_id == row.id
            )
        )
        session.add_all(
            [
                SearchLexicalPosting(passage_id=row.id, term=term, frequency=frequency)
                for term, frequency in terms.items()
            ]
        )
        session.exec(
            update(SearchLexicalState)
            .where(SearchLexicalState.id == 1)
            .values(
                document_count=SearchLexicalState.document_count
                + int(length > 0)
                - int(old_length > 0),
                total_length=SearchLexicalState.total_length + length - old_length,
            )
        )
    row.token_count = length
    session.add(row)
    session.flush()
    if session.get_bind().dialect.name != "sqlite" or current.native_phase not in {
        "building",
        "ready",
    }:
        return
    try:
        with session.begin_nested():
            _fts_replace(session, row.id, old, None if deleted else snapshot(row))
    except DBAPIError:
        _mark_broken(session)


def rebuild_partition(session: Session, *, limit: int = 64) -> int:
    """Create/probe the native index and materialize at most one bounded page."""
    if not 1 <= limit <= 1024:
        raise ValueError("search_rebuild_limit")
    current = state(session)
    if session.get_bind().dialect.name != "sqlite":
        current.native_phase = "ready"
        session.add(current)
        session.flush()
        return 0
    try:
        with session.begin_nested():
            if current.native_phase in {"absent", "broken"} or not _fts_exists(session):
                session.execute(text("DROP TABLE IF EXISTS search_passages_fts"))
                session.execute(
                    text(
                        "CREATE VIRTUAL TABLE search_passages_fts USING fts5(title,tags_text,text,content='search_passages',content_rowid='id',tokenize='unicode61 remove_diacritics 0')"
                    )
                )
                current.native_phase = "building"
                current.native_after_id = 0
                current.failure_code = None
            if current.native_phase == "ready":
                return 0
            rows = session.exec(
                select(SearchPassage)
                .where(SearchPassage.id > current.native_after_id, canonical_passage())
                .order_by(SearchPassage.id)
                .limit(limit)
            ).all()
            for row in rows:
                assert row.id is not None
                fields = snapshot(row)
                _fts_replace(session, row.id, fields, fields)
            if rows:
                current.native_after_id = int(rows[-1].id)
            if len(rows) < limit:
                current.native_phase = "ready"
            session.add(current)
            session.flush()
            return len(rows)
    except DBAPIError:
        _mark_broken(session)
        return 0


def capability(session: Session) -> str:
    current = session.get(SearchLexicalState, 1)
    if current is None or current.native_phase != "ready":
        return "ranked_like"
    if session.get_bind().dialect.name != "sqlite":
        return "postgres_bm25"
    # The durable status can outlive a restore or extension removal. Preparing
    # a read verifies the table/module without reading any passage content. A
    # savepoint keeps an optional adapter failure out of the caller transaction.
    try:
        with session.begin_nested():
            session.execute(text("SELECT rowid FROM search_passages_fts LIMIT 0"))
    except DBAPIError:
        return "ranked_like"
    return "fts5"


def canonical_passage():
    """Only the newest supported recipe per authorized segment enters BM25."""
    newer = aliased(SearchPassage)
    return (
        ~select(newer.id)
        .where(
            newer.subject_type == SearchPassage.subject_type,
            newer.subject_id == SearchPassage.subject_id,
            newer.visibility_segment_key == SearchPassage.visibility_segment_key,
            newer.recipe_version.in_((1, 2)),
            newer.recipe_version > SearchPassage.recipe_version,
        )
        .correlate(SearchPassage)
        .exists()
    )
