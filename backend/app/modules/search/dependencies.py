"""Bounded extraction inventory, separate from read-time access dependencies."""

from printstash_core.search.passages import MAX_FIELD_ITEMS, SearchSubject, SubjectType
from sqlmodel import Session, col, select

from app.db.models import (
    Collection,
    Document,
    Model,
    MultipartModel,
    MultipartModelChoice,
)
from app.db.projections import ContentSource
from app.modules.search.sources import ModelProjection, project_models

SUBJECT_MODELS = {
    SubjectType.MODEL: Model,
    SubjectType.COLLECTION: Collection,
    SubjectType.DOCUMENT: Document,
    SubjectType.MULTIPART_MODEL: MultipartModel,
}


def _collection_chains(
    session: Session, collection_ids: set[int]
) -> dict[int, set[int]]:
    """Read all ancestry for bounded page roots; UNION terminates cycles."""
    ids = {collection_id: {collection_id} for collection_id in collection_ids}
    if not ids:
        return ids
    chain = (
        select(
            col(Collection.id).label("root_id"),
            col(Collection.id),
            col(Collection.parent_id),
        )
        .where(col(Collection.id).in_(collection_ids))
        .cte("collection_chain", recursive=True)
    )
    chain = chain.union(
        select(chain.c.root_id, col(Collection.id), col(Collection.parent_id)).join(
            chain, col(Collection.id) == chain.c.parent_id
        )
    )
    for root_id, parent_id in session.exec(select(chain.c.root_id, chain.c.parent_id)):
        if parent_id is not None:
            ids[root_id].add(parent_id)
    return ids


def model_dependencies(
    session: Session, models: dict[int, ModelProjection]
) -> dict[int, frozenset[ContentSource]]:
    chains = _collection_chains(
        session,
        {row.collection_id for row in models.values() if row.collection_id is not None},
    )
    result = {}
    for model_id, row in models.items():
        refs = {ContentSource("model", model_id)}
        if row.collection_id is not None:
            refs.update(
                ContentSource("collection", id) for id in chains[row.collection_id]
            )
        for kind, ids in (
            ("file", row.file_ids),
            ("provenance", row.provenance_ids),
            ("tag", row.tag_ids),
        ):
            refs.update(ContentSource(kind, id) for id in ids)
        result[model_id] = frozenset(refs)
    return result


def extraction_dependencies(
    session: Session, subject: SearchSubject
) -> set[ContentSource]:
    if subject.subject_type is SubjectType.MODEL:
        models = project_models(session, (subject.subject_id,))
        refs = model_dependencies(session, models).get(subject.subject_id)
        return set(refs) if refs is not None else set()
    row = session.get(SUBJECT_MODELS[subject.subject_type], subject.subject_id)
    if row is None:
        return set()
    refs = {ContentSource(subject.subject_type.value, subject.subject_id)}
    collection_id = row.id if isinstance(row, Collection) else row.collection_id
    if collection_id is not None:
        refs.update(
            ContentSource("collection", ancestor)
            for ancestor in _collection_chains(session, {collection_id})[collection_id]
        )
    if isinstance(row, MultipartModel):
        members = session.exec(
            select(MultipartModelChoice.model_id)
            .where(MultipartModelChoice.multipart_model_id == row.id)
            .order_by(
                MultipartModelChoice.multipart_part_id,
                MultipartModelChoice.sort_order,
                MultipartModelChoice.id,
            )
            .limit(MAX_FIELD_ITEMS + 1)
        ).all()
        refs.update(ContentSource("model", id) for id in members)
    return refs
