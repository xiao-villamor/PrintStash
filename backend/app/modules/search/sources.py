"""The recipe-v1 library field list, with separately authorized contributors.

No bytes, inference sessions, Family data or user-specific indexes are read here.
Callers own the transaction and must revalidate Subject and segment access before
using these internal projections in retrieval or returning them to a user.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime

from printstash_core.search.passages import (
    MAX_FIELD_ITEMS,
    PassageContent,
    SearchSubject,
    SubjectType,
)
from sqlmodel import Session, select

from app.db.models import (
    SENTINEL_MODEL_HASH,
    Collection,
    Document,
    DocumentKind,
    File,
    FileType,
    Model,
    ModelProvenanceField,
    ModelProvenanceSource,
    MultipartModel,
    MultipartModelChoice,
    MultipartPart,
    Tag,
)
from app.db.scopes import live
from app.modules.library.library_search import effective_tag_pairs
from app.modules.library.provenance import effective_value


@dataclass(frozen=True)
class PassageSegment:
    content: PassageContent
    access_dependencies: tuple[SearchSubject, ...] = ()
    truncated: bool = False


@dataclass(frozen=True)
class SubjectProjection:
    segments: tuple[PassageSegment, ...]
    source_updated_at: datetime


def _collection_path(session: Session, collection_id: int | None) -> str:
    return (
        session.exec(
            select(Collection.path).where(
                Collection.id == collection_id, live(Collection)
            )
        ).first()
        or ""
    )


def _provenance(
    session: Session, model_id: int
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...], bool]:
    fields = session.exec(
        select(ModelProvenanceField)
        .join(
            ModelProvenanceSource,
            ModelProvenanceSource.id == ModelProvenanceField.provenance_source_id,
        )
        .where(
            ModelProvenanceSource.model_id == model_id,
            ModelProvenanceField.field_name.in_(["title", "summary", "description"]),
        )
        .order_by(ModelProvenanceSource.id, ModelProvenanceField.field_name)
        .limit(MAX_FIELD_ITEMS + 1)
    ).all()
    titles: list[str] = []
    summaries: list[str] = []
    for field in fields[:MAX_FIELD_ITEMS]:
        value = effective_value(field)
        if isinstance(value, str):
            (titles if field.field_name == "title" else summaries).append(value)
    sources = session.exec(
        select(ModelProvenanceSource.tags_json)
        .where(ModelProvenanceSource.model_id == model_id)
        .order_by(ModelProvenanceSource.id)
        .limit(MAX_FIELD_ITEMS + 1)
    ).all()
    tags: list[str] = []
    truncated = len(fields) > MAX_FIELD_ITEMS or len(sources) > MAX_FIELD_ITEMS
    for encoded in sources[:MAX_FIELD_ITEMS]:
        values = json.loads(encoded)
        if isinstance(values, list):
            truncated |= len(values) > MAX_FIELD_ITEMS
            tags.extend(
                value for value in values[:MAX_FIELD_ITEMS] if isinstance(value, str)
            )
    return tuple(titles), tuple(summaries), tuple(tags), truncated


def _model(session: Session, subject_id: int) -> SubjectProjection | None:
    model = session.exec(
        select(Model).where(
            Model.id == subject_id, live(Model), Model.hash != SENTINEL_MODEL_HASH
        )
    ).first()
    if model is None:
        return None
    pairs = effective_tag_pairs().subquery()
    tags = session.exec(
        select(Tag.name)
        .join(pairs, pairs.c.tag_id == Tag.id)
        .where(pairs.c.model_id == subject_id)
        .distinct()
        .order_by(Tag.name)
        .limit(MAX_FIELD_ITEMS + 1)
    ).all()
    files = session.exec(
        select(File)
        .where(File.model_id == subject_id, live(File))
        .order_by(File.version, File.id)
        .limit(MAX_FIELD_ITEMS + 1)
    ).all()
    titles, summaries, source_tags, truncated = _provenance(session, subject_id)
    content = PassageContent(
        title=model.name,
        collection=_collection_path(session, model.collection_id),
        tags=tuple(tags),
        description=model.description or "",
        filenames=tuple(file.original_filename for file in files),
        revisions=tuple(
            "\n".join(
                value for value in (file.revision_label, file.revision_notes) if value
            )
            for file in files
            if file.file_type == FileType.GCODE
        ),
        source_titles=titles,
        source_summaries=summaries,
        source_tags=source_tags,
    )
    return SubjectProjection(
        (PassageSegment(content, truncated=truncated),), model.updated_at
    )


def _collection(session: Session, subject_id: int) -> SubjectProjection | None:
    collection = session.exec(
        select(Collection).where(Collection.id == subject_id, live(Collection))
    ).first()
    if collection is None:
        return None
    return SubjectProjection(
        (
            PassageSegment(
                PassageContent(
                    title=collection.name,
                    collection=collection.path,
                    description=collection.readme or "",
                )
            ),
        ),
        collection.created_at,
    )


def _multipart(session: Session, subject_id: int) -> SubjectProjection | None:
    multipart = session.get(MultipartModel, subject_id)
    if multipart is None:
        return None
    parts = session.exec(
        select(MultipartPart.name)
        .where(MultipartPart.multipart_model_id == subject_id)
        .order_by(MultipartPart.sort_order, MultipartPart.id)
        .limit(MAX_FIELD_ITEMS + 1)
    ).all()
    choices = session.exec(
        select(Model.id, Model.name, MultipartModelChoice.label)
        .join(MultipartModelChoice, MultipartModelChoice.model_id == Model.id)
        .where(
            MultipartModelChoice.multipart_model_id == subject_id,
            live(Model),
            Model.hash != SENTINEL_MODEL_HASH,
        )
        .order_by(
            MultipartModelChoice.multipart_part_id,
            MultipartModelChoice.sort_order,
            MultipartModelChoice.id,
        )
        .limit(MAX_FIELD_ITEMS + 1)
    ).all()
    segments = [
        PassageSegment(
            PassageContent(
                title=multipart.name,
                collection=_collection_path(session, multipart.collection_id),
                description=multipart.description or "",
                parts=tuple(parts),
            ),
            truncated=len(choices) > MAX_FIELD_ITEMS,
        )
    ]
    # Never place member names/labels in the shared segment. Permission changes
    # must take effect at read time even before the next projection rebuild.
    members: dict[int, list[str]] = {}
    for model_id, name, label in choices[:MAX_FIELD_ITEMS]:
        assert model_id is not None
        members.setdefault(model_id, []).extend(
            value for value in (name, label) if value
        )
    for model_id, names in members.items():
        segments.append(
            PassageSegment(
                PassageContent(title=multipart.name, choices=tuple(names)),
                (SearchSubject(SubjectType.MODEL, model_id),),
            )
        )
    return SubjectProjection(tuple(segments), multipart.updated_at)


def _document(session: Session, subject_id: int) -> SubjectProjection | None:
    document = session.exec(
        select(Document).where(Document.id == subject_id, live(Document))
    ).first()
    if document is None:
        return None
    content = PassageContent(
        title=document.name,
        body=(document.body or "") if document.kind == DocumentKind.MARKDOWN else "",
        filenames=(document.filename,)
        if document.filename and document.kind != DocumentKind.MARKDOWN
        else (),
    )
    return SubjectProjection((PassageSegment(content),), document.updated_at)


_LOADERS = {
    SubjectType.MODEL: _model,
    SubjectType.COLLECTION: _collection,
    SubjectType.MULTIPART_MODEL: _multipart,
    SubjectType.DOCUMENT: _document,
}


def project_subject(
    session: Session, subject: SearchSubject
) -> SubjectProjection | None:
    """Read the complete supported recipe for a live Subject, without committing."""
    return _LOADERS[subject.subject_type](session, subject.subject_id)
