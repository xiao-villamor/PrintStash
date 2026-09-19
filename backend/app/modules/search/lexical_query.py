"""Rank authorized passages using FTS5, real PostgreSQL BM25, or escaped LIKE."""

from __future__ import annotations

from printstash_core.search.lexical import fts_query, query_terms
from sqlalchemy import (
    Float,
    case,
    cast,
    column,
    false,
    func,
    literal_column,
    or_,
    table,
    text,
)
from sqlalchemy.sql.visitors import cloned_traverse
from sqlmodel import Session, select

from app.db.models import (
    SearchLexicalPosting,
    SearchLexicalState,
    SearchLexicalTerm,
    SearchPassage,
)
from app.modules.search.access import passage_in_scope
from app.modules.search.lexical_index import canonical_passage, capability


def escaped_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def ranked_like(query: str, allowed_ids):
    query = query.strip()
    pattern = "%" + escaped_like(query) + "%"
    score = case(
        (func.lower(SearchPassage.title) == query.lower(), 100.0),
        (SearchPassage.title.ilike(escaped_like(query) + "%", escape="\\"), 80.0),
        (SearchPassage.tags_text.ilike(pattern, escape="\\"), 60.0),
        (SearchPassage.title.ilike(pattern, escape="\\"), 40.0),
        else_=10.0,
    )
    return select(SearchPassage.id.label("passage_id"), score.label("score")).where(
        passage_in_scope(allowed_ids),
        or_(
            SearchPassage.title.ilike(pattern, escape="\\"),
            SearchPassage.tags_text.ilike(pattern, escape="\\"),
            SearchPassage.text.ilike(pattern, escape="\\"),
        ),
    )


def _original_statement(
    session: Session, query: str, allowed_ids, *, force_like: bool = False
):
    allowed_ids = select(SearchPassage.id).where(
        passage_in_scope(allowed_ids), canonical_passage()
    )
    terms = query_terms(query)
    backend = "ranked_like" if force_like else capability(session)
    if not query.strip():
        return ranked_like(query, allowed_ids).where(false())
    if backend == "ranked_like" or not terms:
        return ranked_like(query, allowed_ids)
    if backend == "fts5":
        fts = table("search_passages_fts", column("rowid"))
        score = -func.bm25(literal_column("search_passages_fts"), 5.0, 3.0, 1.0)
        return (
            select(SearchPassage.id.label("passage_id"), score.label("score"))
            .join(fts, fts.c.rowid == SearchPassage.id)
            .where(
                passage_in_scope(allowed_ids),
                text("search_passages_fts MATCH :lexical_query"),
            )
            .params(lexical_query=fts_query(query))
        )
    # GIN tsvector supplies candidates. These postings and corpus statistics
    # implement BM25; PostgreSQL's ts_rank is intentionally not the scorer.
    corpus = session.get(SearchLexicalState, 1)
    if corpus is None or corpus.document_count <= 0 or corpus.total_length <= 0:
        return ranked_like(query, allowed_ids)
    df = cast(SearchLexicalTerm.document_frequency, Float)
    frequency = SearchLexicalPosting.frequency
    idf = func.greatest(1e-6, func.ln((corpus.document_count - df + 0.5) / (df + 0.5)))
    denominator = frequency + 1.2 * (
        0.25
        + 0.75
        * SearchPassage.token_count
        / (corpus.total_length / corpus.document_count)
    )
    score = func.sum(idf * frequency * 2.2 / denominator)
    tsquery = " | ".join("'" + term.replace("'", "''") + "'" for term in terms)
    return (
        select(SearchPassage.id.label("passage_id"), score.label("score"))
        .join(SearchLexicalPosting, SearchLexicalPosting.passage_id == SearchPassage.id)
        .join(SearchLexicalTerm, SearchLexicalTerm.term == SearchLexicalPosting.term)
        .where(
            passage_in_scope(allowed_ids),
            SearchLexicalPosting.term.in_(terms),
            SearchPassage.lexemes.op("@@")(func.to_tsquery("simple", tsquery)),
        )
        .group_by(SearchPassage.id)
    )


def ranked_statement(
    session: Session, query: str, allowed_ids, *, force_like: bool = False
):
    from app.modules.search.expansion import with_expansion

    allowed = select(SearchPassage.id).where(
        passage_in_scope(allowed_ids), canonical_passage()
    )
    return with_expansion(
        session,
        query,
        allowed,
        _original_statement(session, query, allowed, force_like=force_like),
    )


def ordered_passages(
    session: Session,
    query: str,
    allowed_ids,
    *,
    limit: int = 256,
    force_like: bool = False,
):
    if not 1 <= limit <= 2048:
        raise ValueError("search_candidate_limit")
    statement = ranked_statement(session, query, allowed_ids, force_like=force_like)
    return statement.order_by(literal_column("score").desc(), SearchPassage.id).limit(
        limit
    )


class LibrarySearch:
    def model_matches(self, session: Session, query: str, allowed_model_ids):
        allowed = select(SearchPassage.id).where(
            SearchPassage.subject_type == "model",
            SearchPassage.subject_id.in_(allowed_model_ids),
            SearchPassage.access_dependencies_json == "[]",
        )
        ranks = ranked_statement(session, query, allowed).cte()
        # SQLite cannot evaluate the FTS5 auxiliary bm25() inside an aggregate
        # after subquery flattening. Materialize its per-passage scores first.
        if session.get_bind().dialect.name == "sqlite":
            # Clone ancestry retains the anonymous name's original owner when
            # prefix_with() makes its generative copy (including Python 3.13).
            ranks = cloned_traverse(ranks, {}, {}).prefix_with("MATERIALIZED")
        return (
            select(
                SearchPassage.subject_id.label("model_id"),
                func.max(ranks.c.score).label("score"),
            )
            .join(ranks, ranks.c.passage_id == SearchPassage.id)
            .group_by(SearchPassage.subject_id)
            .subquery()
        )
