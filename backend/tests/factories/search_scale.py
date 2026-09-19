"""Bounded replication of real, unfiled Model passages for scale measurements.

Replicas preserve the measured text/vector pair. They increase cardinality, not
semantic diversity; benchmark reports must distinguish those two properties.
"""

import hashlib

from sqlalchemy import delete, func, insert
from sqlmodel import select

from app.db.models import Model, PassageVector, SearchPassage
from app.db.models.search import (
    SearchDependency,
    SearchLexicalPosting,
    SearchLexicalTerm,
)
from app.modules.search import lexical_index


def _values(row):
    return {
        column.name: getattr(row, column.name)
        for column in row.__table__.columns
        if column.name != "id" and column.computed is None
    }


def replicate_models(session, seeds, generation, *, count):
    """Expand simple factory Models to exactly count rows in the caller's transaction."""
    if not seeds or not len(seeds) <= count <= 100_000:
        raise ValueError("search_scale_count_invalid")
    templates = []
    for model in seeds:
        if model.collection_id is not None or model.deleted_at is not None:
            raise ValueError("search_scale_unfiled_live_model_required")
        passage = session.exec(
            select(SearchPassage).where(
                SearchPassage.subject_type == "model",
                SearchPassage.subject_id == model.id,
            )
        ).one()
        vector = (
            session.exec(
                select(PassageVector).where(
                    PassageVector.passage_id == passage.id,
                    PassageVector.generation_id == generation.id,
                )
            ).one()
            if generation is not None
            else None
        )
        dependencies = session.exec(
            select(SearchDependency).where(
                SearchDependency.subject_type == "model",
                SearchDependency.subject_id == model.id,
            )
        ).all()
        if passage.access_dependencies_json != "[]" or any(
            row.source_kind != "model" or row.source_id != model.id
            for row in dependencies
        ):
            raise ValueError("search_scale_simple_context_required")
        postings = session.exec(
            select(SearchLexicalPosting).where(
                SearchLexicalPosting.passage_id == passage.id
            )
        ).all()
        templates.append(
            (
                _values(model),
                _values(passage),
                _values(vector) if vector is not None else None,
                [_values(row) for row in postings],
            )
        )
    starts = {
        cls: (session.exec(select(func.max(cls.id))).one() or 0) + 1
        for cls in (Model, SearchPassage, PassageVector)
    }
    for offset in range(0, count - len(seeds), 256):
        models, passages, vectors, dependencies, postings = [], [], [], [], []
        for index in range(offset, min(offset + 256, count - len(seeds))):
            model, passage, vector, terms = templates[index % len(templates)]
            model_id, passage_id, vector_id = (
                starts[cls] + index for cls in (Model, SearchPassage, PassageVector)
            )
            models.append(
                model
                | {
                    "id": model_id,
                    "slug": f"scale-replica-{model_id}",
                    "hash": hashlib.sha256(
                        f"scale-replica-{model_id}".encode()
                    ).hexdigest(),
                }
            )
            passages.append(passage | {"id": passage_id, "subject_id": model_id})
            if vector is not None:
                vectors.append(
                    vector
                    | {
                        "id": vector_id,
                        "unit_key": f"passage:{passage_id}",
                        "passage_id": passage_id,
                        "subject_id": model_id,
                        "model_id": model_id,
                    }
                )
            dependencies.append(
                {
                    "subject_type": "model",
                    "subject_id": model_id,
                    "source_kind": "model",
                    "source_id": model_id,
                }
            )
            postings.extend(term | {"passage_id": passage_id} for term in terms)
        for cls, rows in (
            (Model, models),
            (SearchPassage, passages),
            (PassageVector, vectors),
            (SearchDependency, dependencies),
            (SearchLexicalPosting, postings),
        ):
            if rows:
                session.execute(insert(cls.__table__), rows)
    session.exec(delete(SearchLexicalTerm))
    session.execute(
        insert(SearchLexicalTerm.__table__).from_select(
            ["term", "document_frequency"],
            select(SearchLexicalPosting.term, func.count()).group_by(
                SearchLexicalPosting.term
            ),
        )
    )
    state = lexical_index.state(session)
    state.document_count, state.total_length = session.exec(
        select(func.count(SearchPassage.id), func.sum(SearchPassage.token_count))
    ).one()
    state.native_phase, state.native_after_id = "broken", 0
    session.add(state)
    session.flush()
    return {
        "first_replica_id": starts[Model],
        "replicas": count - len(seeds),
        "seed_ids": [model.id for model in seeds],
    }
