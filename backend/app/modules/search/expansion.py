"""Separate weighted lexical expansion; reads never load a model or infer text."""

from printstash_core.search.lexical import query_terms
from sqlalchemy import func, union_all
from sqlalchemy.sql.visitors import cloned_traverse
from sqlmodel import Session, select

from app.db.models import SearchExpansion, SearchExpansionTerm, SearchPassage

RANK_K = 60
CONTRIBUTION_CAP = 0.25
# Conservative row/index allowance, shared with the total search-index budget.
BYTES_PER_PASSAGE = 1024 + 128 * 256


def identity(session: Session) -> str | None:
    from importlib.util import find_spec

    if find_spec("app.modules.inference") is None:
        return None
    from app.modules.search.configuration import settings

    config = settings(session)
    return (
        config.sparse_model_id
        if config.enabled
        and config.local_models_enabled
        and config.sparse_expansion_enabled
        else None
    )


def occupied_bytes(session: Session) -> int:
    return (
        session.exec(select(func.count()).select_from(SearchExpansion)).one()
        * BYTES_PER_PASSAGE
    )


def with_expansion(session: Session, query: str, allowed_ids, original):
    recipe = identity(session)
    terms = query_terms(query)
    if recipe is None or not terms:
        return original
    lexical = original.cte()
    if session.get_bind().dialect.name == "sqlite":
        # Preserve SQLAlchemy's clone ancestry before prefix_with() copies the
        # node: its anonymous name must not outlive the identity that owns it.
        # Keep CTEs hoisted; nesting this query overflows older SQLite parsers.
        lexical = cloned_traverse(lexical, {}, {}).prefix_with("MATERIALIZED")
    original_rank = select(
        lexical.c.passage_id,
        (
            1.0
            / (
                RANK_K
                + func.row_number().over(
                    order_by=(lexical.c.score.desc(), lexical.c.passage_id)
                )
            )
        ).label("score"),
    )
    sparse = (
        select(
            SearchExpansion.passage_id.label("passage_id"),
            (func.sum(SearchExpansionTerm.weight) / (10.0 * len(terms))).label(
                "weight"
            ),
        )
        .join(
            SearchExpansionTerm,
            SearchExpansionTerm.passage_id == SearchExpansion.passage_id,
        )
        .join(SearchPassage, SearchPassage.id == SearchExpansion.passage_id)
        .where(
            SearchExpansion.passage_id.in_(allowed_ids),
            SearchExpansion.recipe == recipe,
            SearchExpansion.phase == "ready",
            SearchExpansion.input_hash == SearchPassage.content_hash,
            SearchExpansionTerm.term.in_(terms),
        )
        .group_by(SearchExpansion.passage_id)
        .cte()
    )
    sparse_rank = select(
        sparse.c.passage_id,
        (
            CONTRIBUTION_CAP
            * sparse.c.weight
            / (
                RANK_K
                + func.row_number().over(
                    order_by=(sparse.c.weight.desc(), sparse.c.passage_id)
                )
            )
        ).label("score"),
    )
    candidates = union_all(original_rank, sparse_rank).cte()
    return (
        select(
            SearchPassage.id.label("passage_id"),
            func.sum(candidates.c.score).label("score"),
        )
        .join(candidates, candidates.c.passage_id == SearchPassage.id)
        .group_by(SearchPassage.id)
    )
