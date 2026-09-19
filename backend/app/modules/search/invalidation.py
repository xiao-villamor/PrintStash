"""Content transactions discard stale semantic derivatives in bounded batches."""

from sqlalchemy import delete
from sqlmodel import Session, col, select

from app.db.models import IndexGeneration, PassageVector, SearchIndexFailure
from app.modules.search import vector_index


def invalidate_passage(session: Session, passage_id: int) -> int:
    removed = 0
    while True:
        rows = session.exec(
            select(PassageVector.id, PassageVector.generation_id)
            .where(PassageVector.passage_id == passage_id)
            .order_by(PassageVector.id)
            .limit(128)
        ).all()
        if not rows:
            break
        for vector_id, generation_id in rows:
            generation = session.get(IndexGeneration, generation_id)
            if generation is not None:
                vector_index.remove(session, generation, vector_id)
        session.exec(
            delete(PassageVector).where(
                col(PassageVector.id).in_([id for id, _ in rows])
            )
        )
        removed += len(rows)
    session.exec(
        delete(SearchIndexFailure).where(SearchIndexFailure.passage_id == passage_id)
    )
    return removed
