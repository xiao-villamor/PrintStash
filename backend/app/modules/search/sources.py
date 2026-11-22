"""The recipe-v1 library field list, with separately authorized contributors.

No bytes, inference sessions or user-specific indexes are read here.
Callers own the transaction and must revalidate Subject and segment access before
using these internal projections in retrieval or returning them to a user.
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from printstash_core.search.passages import (
    MAX_FIELD_ITEMS,
    PassageContent,
    SearchSubject,
    SubjectType,
)
from sqlalchemy import func
from sqlmodel import Session, col, select

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
    fields: Sequence[ModelProvenanceField], sources: Sequence[str]
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...], bool]:
    titles: list[str] = []
    summaries: list[str] = []
    for field in fields[:MAX_FIELD_ITEMS]:
        value = effective_value(field)
        if isinstance(value, str):
            (titles if field.field_name == "title" else summaries).append(value)
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


@dataclass(frozen=True)
class ModelProjection:
    projection: SubjectProjection
    collection_id: int | None
    file_ids: tuple[int, ...]
    provenance_ids: tuple[int, ...]
    tag_ids: tuple[int, ...]


def project_models(
    session: Session, model_ids: tuple[int, ...]
) -> dict[int, ModelProjection]:
    """Read a bounded page of Models and the contributors to their text."""
    if not model_ids:
        return {}
    # Preparation already resolved a bounded, deduplicated keyset page across
    # sources. Reuse the same keys in each query and rank contributors per Model
    # so even one unusually large Model cannot expand the page arbitrarily.
    models = (
        select(col(Model.id).label("id"))
        .where(
            col(Model.id).in_(model_ids), live(Model), Model.hash != SENTINEL_MODEL_HASH
        )
        .cte("projection_models")
    )
    scope = select(models.c.id)
    model_rows = session.exec(
        select(Model, col(Collection.path))
        .outerjoin(
            Collection,
            (col(Collection.id) == col(Model.collection_id)) & live(Collection),
        )
        .where(col(Model.id).in_(scope))
    ).all()
    if not model_rows:
        return {}
    pairs = effective_tag_pairs(model_scope=scope).subquery()
    distinct_tags = (
        select(pairs.c.model_id, col(Tag.id).label("id"), col(Tag.name).label("name"))
        .join(Tag, col(Tag.id) == pairs.c.tag_id)
        .distinct()
        .subquery()
    )
    tag_rank = func.row_number().over(
        partition_by=distinct_tags.c.model_id,
        order_by=(distinct_tags.c.name, distinct_tags.c.id),
    )
    ranked_tags = select(distinct_tags, tag_rank.label("rank")).subquery()
    tags: dict[int, list[tuple[int, str]]] = defaultdict(list)
    for model_id, tag_id, name in session.exec(
        select(ranked_tags.c.model_id, ranked_tags.c.id, ranked_tags.c.name)
        .where(ranked_tags.c.rank <= MAX_FIELD_ITEMS + 1)
        .order_by(ranked_tags.c.model_id, ranked_tags.c.rank)
    ):
        tags[model_id].append((tag_id, name))

    file_rank = func.row_number().over(
        partition_by=col(File.model_id), order_by=(col(File.version), col(File.id))
    )
    ranked_files = (
        select(
            File.model_id,
            File.id,
            File.original_filename,
            File.file_type,
            File.revision_label,
            File.revision_notes,
            file_rank.label("rank"),
        )
        .where(col(File.model_id).in_(scope), live(File))
        .subquery()
    )
    filenames: dict[int, list[str]] = defaultdict(list)
    revisions: dict[int, list[str]] = defaultdict(list)
    file_ids: dict[int, list[int]] = defaultdict(list)
    for model_id, file_id, filename, file_type, label, notes in session.exec(
        select(
            ranked_files.c.model_id,
            ranked_files.c.id,
            ranked_files.c.original_filename,
            ranked_files.c.file_type,
            ranked_files.c.revision_label,
            ranked_files.c.revision_notes,
        )
        .where(ranked_files.c.rank <= MAX_FIELD_ITEMS + 1)
        .order_by(ranked_files.c.model_id, ranked_files.c.rank)
    ):
        file_ids[model_id].append(file_id)
        filenames[model_id].append(filename)
        if file_type == FileType.GCODE:
            revisions[model_id].append(
                "\n".join(value for value in (label, notes) if value)
            )

    source_rank = func.row_number().over(
        partition_by=col(ModelProvenanceSource.model_id),
        order_by=col(ModelProvenanceSource.id),
    )
    ranked_sources = (
        select(
            ModelProvenanceSource.model_id,
            ModelProvenanceSource.id,
            ModelProvenanceSource.tags_json,
            source_rank.label("rank"),
        )
        .where(col(ModelProvenanceSource.model_id).in_(scope))
        .subquery()
    )
    source_ids: dict[int, list[int]] = defaultdict(list)
    source_tags: dict[int, list[str]] = defaultdict(list)
    for model_id, source_id, tags_json in session.exec(
        select(
            ranked_sources.c.model_id, ranked_sources.c.id, ranked_sources.c.tags_json
        )
        .where(ranked_sources.c.rank <= MAX_FIELD_ITEMS + 1)
        .order_by(ranked_sources.c.model_id, ranked_sources.c.rank)
    ):
        source_ids[model_id].append(source_id)
        source_tags[model_id].append(tags_json)

    field_rank = func.row_number().over(
        partition_by=col(ModelProvenanceSource.model_id),
        order_by=(col(ModelProvenanceSource.id), col(ModelProvenanceField.field_name)),
    )
    ranked_fields = (
        select(
            ModelProvenanceSource.model_id,
            ModelProvenanceField.id,
            field_rank.label("rank"),
        )
        .join(
            ModelProvenanceField,
            col(ModelProvenanceField.provenance_source_id)
            == col(ModelProvenanceSource.id),
        )
        .where(
            col(ModelProvenanceSource.model_id).in_(scope),
            col(ModelProvenanceField.field_name).in_(
                ("title", "summary", "description")
            ),
        )
        .subquery()
    )
    fields: dict[int, list[ModelProvenanceField]] = defaultdict(list)
    for model_id, field in session.exec(
        select(ranked_fields.c.model_id, ModelProvenanceField)
        .join(ModelProvenanceField, col(ModelProvenanceField.id) == ranked_fields.c.id)
        .where(ranked_fields.c.rank <= MAX_FIELD_ITEMS + 1)
        .order_by(ranked_fields.c.model_id, ranked_fields.c.rank)
    ):
        fields[model_id].append(field)

    results = {}
    for model, collection_path in model_rows:
        assert model.id is not None
        id = model.id
        titles, summaries, provided_tags, truncated = _provenance(
            fields[id], source_tags[id]
        )
        content = PassageContent(
            title=model.name,
            collection=collection_path if collection_path is not None else "",
            tags=tuple(name for _, name in tags[id]),
            description=model.description if model.description is not None else "",
            filenames=tuple(filenames[id]),
            revisions=tuple(revisions[id]),
            source_titles=titles,
            source_summaries=summaries,
            source_tags=provided_tags,
        )
        results[id] = ModelProjection(
            SubjectProjection(
                (PassageSegment(content, truncated=truncated),), model.updated_at
            ),
            model.collection_id,
            tuple(file_ids[id]),
            tuple(source_ids[id]),
            tuple(tag_id for tag_id, _ in tags[id]),
        )
    return results


def _model(session: Session, subject_id: int) -> SubjectProjection | None:
    model = project_models(session, (subject_id,)).get(subject_id)
    return model.projection if model is not None else None


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
    multipart = session.exec(
        select(MultipartModel).where(
            MultipartModel.id == subject_id, live(MultipartModel)
        )
    ).first()
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
