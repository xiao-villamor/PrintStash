"""Composition service for standalone multipart models.

Multipart models are references only.  They never own or move a Model's files,
revisions, thumbnails, or trash state.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Iterable

from fastapi import HTTPException
from sqlalchemy import func
from sqlmodel import Session, select

from app.db.models import (
    SENTINEL_MODEL_HASH,
    Collection,
    CollectionRole,
    Document,
    File,
    FileType,
    Model,
    MultipartModel,
    MultipartModelChoice,
    MultipartPart,
    User,
)
from app.db.scopes import live
from app.schemas.documents import DocumentListItem
from app.schemas.multipart_models import (
    MultipartChoiceWrite,
    MultipartMemberRead,
    MultipartModelListItem,
    MultipartModelRead,
    MultipartPartRead,
    MultipartPartWrite,
)
from app.services import model_views, rbac


class MultipartModelError(ValueError):
    """A validation failure that maps to a stable API error code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _collection_path(session: Session, collection_id: int | None) -> str | None:
    if collection_id is None:
        return None
    collection = session.get(Collection, collection_id)
    return collection.path if collection is not None else None


def _guides(
    session: Session, user: User, multipart_model_id: int
) -> list[DocumentListItem]:
    rows = session.exec(
        select(Document)
        .where(Document.multipart_model_id == multipart_model_id, live(Document))
        .order_by(Document.updated_at.desc(), Document.id.desc())  # type: ignore[attr-defined]
    ).all()
    return [
        DocumentListItem(
            id=int(row.id),
            name=row.name,
            kind=row.kind,
            collection=_collection_path(session, row.collection_id),
            collection_id=row.collection_id,
            multipart_model_id=row.multipart_model_id,
            filename=row.filename,
            effective_role=rbac.effective_collection_role(
                session, user, row.collection_id
            ),
            updated_at=row.updated_at,
        )
        for row in rows
        if row.id is not None
    ]


def _file_counts(
    session: Session, model_ids: Iterable[int]
) -> dict[int, tuple[int, int]]:
    ids = list(model_ids)
    counts: dict[int, tuple[int, int]] = defaultdict(lambda: (0, 0))
    if not ids:
        return counts
    rows = session.exec(
        select(File.model_id, File.file_type, func.count(File.id))
        .where(File.model_id.in_(ids), live(File))  # type: ignore[union-attr]
        .group_by(File.model_id, File.file_type)
    ).all()
    for model_id, file_type, count in rows:
        source_count, gcode_count = counts[int(model_id)]
        if file_type == FileType.GCODE:
            gcode_count += int(count)
        else:
            source_count += int(count)
        counts[int(model_id)] = source_count, gcode_count
    return counts


def _readable_models(
    session: Session, user: User, model_ids: Iterable[int]
) -> dict[int, Model]:
    ids = {int(model_id) for model_id in model_ids}
    if not ids:
        return {}
    rows = session.exec(
        select(Model).where(Model.id.in_(ids), live(Model))  # type: ignore[union-attr]
    ).all()
    roles = rbac.effective_roles_for_collections(
        session, user, (row.collection_id for row in rows)
    )
    return {
        int(row.id): row
        for row in rows
        if row.id is not None
        and rbac.role_allows(roles.get(row.collection_id), CollectionRole.VIEW)
    }


def member_read(
    session: Session,
    user: User,
    model: Model | None,
    *,
    model_id: int,
    counts: dict[int, tuple[int, int]],
    choice: MultipartModelChoice | None = None,
) -> MultipartMemberRead:
    """Return full metadata only when the caller can read a live Model."""
    if model is None or model.deleted_at is not None:
        return MultipartMemberRead(
            id=model_id,
            choice_id=choice.id if choice is not None else None,
            available=False,
        )
    role = rbac.effective_collection_role(session, user, model.collection_id)
    if not rbac.role_allows(role, CollectionRole.VIEW):
        return MultipartMemberRead(
            id=model_id,
            choice_id=choice.id if choice is not None else None,
            available=False,
        )
    source_count, gcode_count = counts[model_id]
    return MultipartMemberRead(
        id=model_id,
        choice_id=choice.id if choice is not None else None,
        legacy_label=getattr(choice, "label", None),
        source_file_id=getattr(choice, "source_file_id", None),
        name=model.name,
        slug=model.slug,
        thumbnail_url=model_views.thumb_url(model),
        source_file_count=source_count,
        gcode_revision_count=gcode_count,
        available=True,
    )


def _parts(
    session: Session, user: User, multipart_model_id: int
) -> tuple[list[MultipartPartRead], int]:
    parts = session.exec(
        select(MultipartPart)
        .where(MultipartPart.multipart_model_id == multipart_model_id)
        .order_by(MultipartPart.sort_order.asc(), MultipartPart.id.asc())  # type: ignore[attr-defined]
    ).all()
    choices = session.exec(
        select(MultipartModelChoice)
        .where(MultipartModelChoice.multipart_model_id == multipart_model_id)
        .order_by(
            MultipartModelChoice.multipart_part_id.asc(),
            MultipartModelChoice.sort_order.asc(),
            MultipartModelChoice.id.asc(),
        )  # type: ignore[attr-defined]
    ).all()
    ids = [int(choice.model_id) for choice in choices]
    models = _readable_models(session, user, ids)
    counts = _file_counts(session, ids)
    by_part: dict[int, list[MultipartMemberRead]] = defaultdict(list)
    for choice in choices:
        by_part[int(choice.multipart_part_id)].append(
            member_read(
                session,
                user,
                models.get(choice.model_id),
                model_id=choice.model_id,
                counts=counts,
                choice=choice,
            )
        )
    result = [
        MultipartPartRead(
            id=int(part.id),
            name=part.name,
            sort_order=part.sort_order,
            models=by_part[int(part.id)],
        )
        for part in parts
        if part.id is not None
    ]
    return result, len({choice.model_id for choice in choices})


def _list_item(
    session: Session, user: User, aggregate: MultipartModel
) -> MultipartModelListItem:
    parts, model_count = _parts(session, user, int(aggregate.id))
    cover = next(
        (
            member.thumbnail_url
            for part in parts
            for member in part.models
            if member.available and member.thumbnail_url
        ),
        None,
    )
    guides = _guides(session, user, int(aggregate.id))
    return MultipartModelListItem(
        id=int(aggregate.id),
        name=aggregate.name,
        slug=aggregate.slug,
        description=aggregate.description,
        collection=_collection_path(session, aggregate.collection_id),
        collection_id=aggregate.collection_id,
        part_count=len(parts),
        model_count=model_count,
        guide_count=len(guides),
        cover_thumbnail_url=cover,
        effective_role=rbac.effective_collection_role(
            session, user, aggregate.collection_id
        ),
        updated_at=aggregate.updated_at,
    )


def read(session: Session, user: User, aggregate: MultipartModel) -> MultipartModelRead:
    item = _list_item(session, user, aggregate)
    parts, _ = _parts(session, user, int(aggregate.id))
    return MultipartModelRead(
        **item.model_dump(),
        created_at=aggregate.created_at,
        parts=parts,
        guides=_guides(session, user, int(aggregate.id)),
    )


def require(
    session: Session,
    user: User,
    aggregate_id: int,
    minimum: CollectionRole,
) -> MultipartModel:
    aggregate = session.exec(
        select(MultipartModel).where(MultipartModel.id == aggregate_id)
    ).first()
    if aggregate is None:
        raise HTTPException(status_code=404, detail="multipart_model_not_found")
    rbac.require_collection_role(session, user, aggregate.collection_id, minimum)
    return aggregate


def list_visible(
    session: Session,
    user: User,
    *,
    collection: str | None = None,
    direct: bool = False,
    query: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[MultipartModelListItem]:
    stmt = select(MultipartModel)
    if collection:
        path = collection.strip().strip("/").lower()
        if direct:
            ids = select(Collection.id).where(Collection.path == path)
        else:
            ids = select(Collection.id).where(
                (Collection.path == path) | Collection.path.startswith(path + "/")
            )
        stmt = stmt.where(MultipartModel.collection_id.in_(ids))  # type: ignore[union-attr]
    elif direct:
        stmt = stmt.where(MultipartModel.collection_id.is_(None))  # type: ignore[union-attr]
    if not user.is_superuser:
        ids = rbac.accessible_collection_ids(session, user)
        if not ids:
            return []
        stmt = stmt.where(MultipartModel.collection_id.in_(ids))  # type: ignore[union-attr]
    if query and (needle := query.strip()):
        stmt = stmt.where(MultipartModel.name.ilike(f"%{needle}%"))  # type: ignore[union-attr]
    rows = session.exec(
        stmt.order_by(MultipartModel.updated_at.desc(), MultipartModel.id.desc())  # type: ignore[attr-defined]
        .offset(offset)
        .limit(limit)
    ).all()
    return [_list_item(session, user, row) for row in rows]


def _choice_inputs(part: MultipartPartWrite) -> list[MultipartChoiceWrite]:
    """Read the current contract, accepting the pre-identity compatibility shape."""
    if part.choices is not None:
        return part.choices
    if part.model_ids is not None:
        return [MultipartChoiceWrite(model_id=model_id) for model_id in part.model_ids]
    raise MultipartModelError("multipart_part_requires_model")


def _prepare_parts(
    session: Session,
    user: User,
    aggregate: MultipartModel,
    requested: list[MultipartPartWrite],
) -> tuple[
    list[tuple[str, list[tuple[int, int | None]]]], dict[int, MultipartModelChoice]
]:
    """Validate a complete replacement without changing the session state."""
    names: set[str] = set()
    requested_rows: list[tuple[str, list[tuple[int, int | None]]]] = []
    member_ids: list[int] = []
    choice_ids: list[int] = []
    for part in requested:
        name = " ".join(part.name.split())
        key = name.casefold()
        if not name or key in names:
            raise MultipartModelError("multipart_part_name_duplicate")
        names.add(key)
        choices = _choice_inputs(part)
        normalized: list[tuple[int, int | None]] = []
        for choice in choices:
            model_id = int(choice.model_id)
            choice_id = int(choice.choice_id) if choice.choice_id is not None else None
            if choice_id is not None and choice_id in choice_ids:
                raise MultipartModelError("multipart_model_duplicate_member")
            choice_ids.extend([choice_id] if choice_id is not None else [])
            normalized.append((model_id, choice_id))
            member_ids.append(model_id)
        requested_rows.append((name, normalized))

    existing = session.exec(
        select(MultipartModelChoice).where(
            MultipartModelChoice.multipart_model_id == aggregate.id
        )
    ).all()
    existing_by_id = {
        int(choice.id): choice for choice in existing if choice.id is not None
    }
    for model_id, choice_id in [
        item for _, choices in requested_rows for item in choices
    ]:
        if choice_id is not None:
            old = existing_by_id.get(choice_id)
            if old is None or old.model_id != model_id:
                raise MultipartModelError("multipart_choice_not_found")

    # Existing choices may be echoed by an editor that cannot read their Model.
    # Only genuinely new choices must pass the live/readable Model check.
    new_member_ids = {
        model_id
        for _, choices in requested_rows
        for model_id, choice_id in choices
        if choice_id is None
    }
    models = (
        session.exec(
            select(Model).where(Model.id.in_(new_member_ids), live(Model))  # type: ignore[union-attr]
        ).all()
        if new_member_ids
        else []
    )
    by_id = {int(model.id): model for model in models if model.id is not None}
    if len(by_id) != len(new_member_ids):
        raise MultipartModelError("multipart_model_member_not_found")
    roles = rbac.effective_roles_for_collections(
        session, user, (model.collection_id for model in models)
    )
    if any(
        not rbac.role_allows(roles.get(model.collection_id), CollectionRole.VIEW)
        for model in models
    ):
        raise MultipartModelError("collection_permission_denied")

    # A Model may occur more than once only when each occurrence is an
    # explicitly preserved legacy file-pinned choice. New duplicate references
    # remain an error, while old data can be edited without collapsing options.
    occurrences: dict[int, list[int | None]] = defaultdict(list)
    for _, choices in requested_rows:
        for model_id, choice_id in choices:
            occurrences[model_id].append(choice_id)
    for _model_id, ids in occurrences.items():
        if len(ids) > 1:
            if any(choice_id is None for choice_id in ids):
                raise MultipartModelError("multipart_model_duplicate_member")
            old_rows = [existing_by_id[int(choice_id)] for choice_id in ids]
            source_ids = [getattr(row, "source_file_id", None) for row in old_rows]
            if any(source_id is None for source_id in source_ids) or len(
                source_ids
            ) != len(set(source_ids)):
                raise MultipartModelError("multipart_model_duplicate_member")
    return requested_rows, existing_by_id


def _apply_parts(
    session: Session,
    aggregate: MultipartModel,
    prepared: tuple[
        list[tuple[str, list[tuple[int, int | None]]]], dict[int, MultipartModelChoice]
    ],
) -> None:
    requested_rows, existing_by_id = prepared
    existing_parts = session.exec(
        select(MultipartPart)
        .where(MultipartPart.multipart_model_id == aggregate.id)
        .order_by(MultipartPart.sort_order.asc(), MultipartPart.id.asc())  # type: ignore[attr-defined]
    ).all()
    used_choice_ids = {
        choice_id
        for _, choices in requested_rows
        for _, choice_id in choices
        if choice_id is not None
    }
    for choice_id, choice in existing_by_id.items():
        if choice_id not in used_choice_ids:
            session.delete(choice)
    session.flush()
    for part in existing_parts[len(requested_rows) :]:
        session.delete(part)
    session.flush()

    # Move existing rows through a temporary namespace to avoid transient
    # unique(sort_order/name_key) collisions while reordering an edit.
    for part in existing_parts[: len(requested_rows)]:
        part.sort_order = -(int(part.id or 1))
        part.name_key = f"__pending_part_{int(part.id or 1)}"
        session.add(part)
    for choice in existing_by_id.values():
        if choice.id in used_choice_ids:
            choice.sort_order = -(int(choice.id or 1))
            session.add(choice)
    session.flush()

    for part_order, (name, choices) in enumerate(requested_rows):
        if part_order < len(existing_parts):
            part_row = existing_parts[part_order]
        else:
            part_row = MultipartPart(
                multipart_model_id=int(aggregate.id),
                name=f"__pending_part_{part_order}",
                name_key=f"__pending_part_{part_order}",
                sort_order=part_order,
            )
            session.add(part_row)
            session.flush()
        part_row.name = name
        part_row.name_key = name.casefold()
        part_row.sort_order = part_order
        session.add(part_row)
        session.flush()
        assert part_row.id is not None
        for choice_order, (model_id, choice_id) in enumerate(choices):
            choice_row = (
                existing_by_id.get(choice_id) if choice_id is not None else None
            )
            if choice_row is None:
                choice_row = MultipartModelChoice(
                    multipart_model_id=int(aggregate.id),
                    multipart_part_id=int(part_row.id),
                    model_id=model_id,
                )
            else:
                choice_row.multipart_part_id = int(part_row.id)
            choice_row.sort_order = choice_order
            session.add(choice_row)


def save(
    session: Session,
    user: User,
    aggregate: MultipartModel,
    requested: list[MultipartPartWrite],
    *,
    name: str | None = None,
    slug: str | None = None,
    description: str | None = None,
    description_set: bool = False,
) -> MultipartModelRead:
    """Validate and persist metadata plus composition in one transaction."""
    prepared = _prepare_parts(session, user, aggregate, requested)
    if name is not None:
        aggregate.name = name
    if slug is not None:
        aggregate.slug = slug
    if description_set:
        aggregate.description = description
    _apply_parts(session, aggregate, prepared)
    aggregate.updated_by = user.id
    from app.core.time import utcnow

    aggregate.updated_at = utcnow()
    session.add(aggregate)
    session.commit()
    session.refresh(aggregate)
    return read(session, user, aggregate)


def replace_parts(
    session: Session,
    user: User,
    aggregate: MultipartModel,
    requested: list[MultipartPartWrite],
) -> MultipartModelRead:
    return save(session, user, aggregate, requested)


def delete_aggregate(session: Session, aggregate: MultipartModel) -> None:
    """Delete only grouping rows; Models and their files remain untouched."""
    session.delete(aggregate)
    session.commit()


def candidates(
    session: Session,
    user: User,
    *,
    query: str | None = None,
    limit: int = 100,
) -> list[MultipartMemberRead]:
    stmt = select(Model).where(live(Model), Model.hash != SENTINEL_MODEL_HASH)
    if not user.is_superuser:
        ids = rbac.accessible_collection_ids(session, user, CollectionRole.VIEW)
        if not ids:
            return []
        stmt = stmt.where(Model.collection_id.in_(ids))  # type: ignore[union-attr]
    if query and (needle := query.strip()):
        stmt = stmt.where(Model.name.ilike(f"%{needle}%"))  # type: ignore[union-attr]
    rows = session.exec(
        stmt.order_by(Model.name.asc(), Model.id.asc()).limit(limit)  # type: ignore[attr-defined]
    ).all()
    counts = _file_counts(
        session, (int(model.id) for model in rows if model.id is not None)
    )
    return [
        member_read(
            session,
            user,
            model,
            model_id=int(model.id),
            counts=counts,
        )
        for model in rows
        if model.id is not None
    ]
