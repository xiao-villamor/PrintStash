"""Bounded extraction inventory, separate from read-time access dependencies."""

from printstash_core.search.passages import MAX_FIELD_ITEMS, SearchSubject, SubjectType
from sqlmodel import Session, select

from app.db.models import (
    Collection,
    Document,
    File,
    Model,
    ModelProvenanceSource,
    MultipartModel,
    MultipartModelChoice,
    Tag,
)
from app.db.projections import ContentSource
from app.db.scopes import live
from app.modules.library.library_search import effective_tag_pairs

SUBJECT_MODELS = {
    SubjectType.MODEL: Model,
    SubjectType.COLLECTION: Collection,
    SubjectType.DOCUMENT: Document,
    SubjectType.MULTIPART_MODEL: MultipartModel,
}


def extraction_dependencies(
    session: Session, subject: SearchSubject
) -> set[ContentSource]:
    row = session.get(SUBJECT_MODELS[subject.subject_type], subject.subject_id)
    if row is None:
        return set()
    refs = {ContentSource(subject.subject_type.value, subject.subject_id)}
    collection_id = row.id if isinstance(row, Collection) else row.collection_id
    seen: set[int] = set()
    while collection_id is not None and collection_id not in seen:
        seen.add(collection_id)
        refs.add(ContentSource("collection", collection_id))
        collection = session.get(Collection, collection_id)
        collection_id = collection.parent_id if collection else None
    if isinstance(row, Model):
        files = session.exec(
            select(File.id)
            .where(File.model_id == row.id, live(File))
            .order_by(File.version, File.id)
            .limit(MAX_FIELD_ITEMS + 1)
        ).all()
        sources = session.exec(
            select(ModelProvenanceSource.id)
            .where(ModelProvenanceSource.model_id == row.id)
            .order_by(ModelProvenanceSource.id)
            .limit(MAX_FIELD_ITEMS + 1)
        ).all()
        pairs = effective_tag_pairs().subquery()
        tags = session.exec(
            select(Tag.id)
            .where(Tag.id.in_(select(pairs.c.tag_id).where(pairs.c.model_id == row.id)))
            .order_by(Tag.name, Tag.id)
            .limit(MAX_FIELD_ITEMS + 1)
        ).all()
        for kind, ids in (("file", files), ("provenance", sources), ("tag", tags)):
            refs.update(ContentSource(kind, id) for id in ids if id is not None)
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
