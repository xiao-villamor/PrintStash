"""Model browse + detail + edit + soft-delete endpoints."""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy import func
from sqlmodel import Session, delete, select

from app.core.security import require_auth
from app.core.time import utcnow
from app.db.models import Category, File, FileType, Metadata, Model, ModelTagLink, Tag
from app.db.session import get_session
from app.schemas.models import (
    FileRead,
    FileRevisionUpdate,
    MetadataRead,
    ModelListItem,
    ModelRead,
    ModelUpdate,
)
from app.services import taxonomy

router = APIRouter(prefix="/models", tags=["models"])


def _tag_names_for(session: Session, model_id: int) -> List[str]:
    stmt = (
        select(Tag.name)
        .join(ModelTagLink, ModelTagLink.tag_id == Tag.id)
        .where(ModelTagLink.model_id == model_id)
        .order_by(Tag.name)
    )
    return list(session.exec(stmt).all())


def _category_name_for(model: Model) -> Optional[str]:
    """Resolve the category name from the FK-joined relationship."""
    return model.category_rel.path if model.category_rel else None


def _thumb_url(model: Model) -> Optional[str]:
    """Stable URL for the model's thumbnail, or None.

    Prefers ``thumbnail_file_id`` (current); falls back to parsing the legacy
    ``thumbnail_path`` for rows written before the file-id column existed.
    """
    if model.thumbnail_file_id:
        return f"/api/v1/files/{model.thumbnail_file_id}/thumbnail"
    if model.thumbnail_path:
        stem = Path(model.thumbnail_path).stem
        if stem.isdigit():
            return f"/api/v1/files/{stem}/thumbnail"
    return None


def _live_model(session: Session, model_id: int) -> Model:
    """Like ``get_or_404`` but also rejects soft-deleted rows."""
    m = session.get(Model, model_id)
    if m is None or m.deleted_at is not None:
        raise HTTPException(status_code=404, detail="model_not_found")
    return m


@router.get(
    "",
    response_model=List[ModelListItem],
    summary="List models",
    description=(
        "List logical models with optional filtering. Soft-deleted models are excluded. "
        "Filter by category (path prefix match, includes descendants), one or more tag "
        "slugs (AND semantics), and/or a name substring."
    ),
)
def list_models(
    category: Optional[str] = Query(
        None, description="Category path e.g. 'functional/brackets'"
    ),
    tag: Optional[List[str]] = Query(
        None, description="Tag slug; repeat for AND-filter"
    ),
    q: Optional[str] = Query(None, description="Substring match on name"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    session: Session = Depends(get_session),
) -> List[ModelListItem]:
    stmt = select(Model).where(Model.deleted_at.is_(None))  # type: ignore[union-attr]

    if category:
        cat_path = category.strip().strip("/").lower()
        # Match the category path or any descendant via FK join on Categories.
        matching_cat_ids = select(Category.id).where(
            (Category.path == cat_path) | (Category.path.startswith(cat_path + "/"))
        )
        stmt = stmt.where(Model.category_id.in_(matching_cat_ids))  # type: ignore[union-attr]

    if q:
        stmt = stmt.where(Model.name.contains(q))  # type: ignore[attr-defined]

    if tag:
        for slug in (t.strip().lower() for t in tag if t.strip()):
            # Each tag adds an EXISTS clause => AND semantics across tags.
            stmt = stmt.where(
                Model.id.in_(  # type: ignore[union-attr]
                    select(ModelTagLink.model_id)
                    .join(Tag, Tag.id == ModelTagLink.tag_id)
                    .where(Tag.slug == slug)
                )
            )

    stmt = stmt.order_by(Model.updated_at.desc()).offset(offset).limit(limit)  # type: ignore[attr-defined]
    rows = session.exec(stmt).all()

    out: List[ModelListItem] = []
    for m in rows:
        file_count = session.exec(
            select(func.count(File.id)).where(File.model_id == m.id)
        ).one()
        out.append(
            ModelListItem(
                id=m.id,  # type: ignore[arg-type]
                name=m.name,
                slug=m.slug,
                category=_category_name_for(m),
                category_id=m.category_id,
                tags=_tag_names_for(session, m.id),  # type: ignore[arg-type]
                thumbnail_url=_thumb_url(m),
                file_count=int(file_count or 0),
                updated_at=m.updated_at,
            )
        )
    return out


def _build_model_read(session: Session, model_id: int) -> ModelRead:
    m = _live_model(session, model_id)

    files_with_meta = session.exec(
        select(File, Metadata)
        .where(File.model_id == model_id)
        .outerjoin(Metadata, Metadata.file_id == File.id)
        .order_by(File.version.asc())  # type: ignore[attr-defined]
    ).all()
    file_reads = [
        FileRead(
            id=f.id,  # type: ignore[arg-type]
            model_id=f.model_id,
            original_filename=f.original_filename,
            file_type=f.file_type,
            version=f.version,
            size_bytes=f.size_bytes,
            sha256=f.sha256,
            revision_status=f.revision_status,
            revision_notes=f.revision_notes,
            is_recommended=f.is_recommended,
            uploaded_at=f.uploaded_at,
            metadata=MetadataRead(**md.model_dump()) if md else None,
        )
        for f, md in files_with_meta
    ]

    return ModelRead(
        id=m.id,  # type: ignore[arg-type]
        name=m.name,
        slug=m.slug,
        hash=m.hash,
        category=_category_name_for(m),
        category_id=m.category_id,
        description=m.description,
        tags=_tag_names_for(session, m.id),  # type: ignore[arg-type]
        thumbnail_url=_thumb_url(m),
        created_at=m.created_at,
        updated_at=m.updated_at,
        files=file_reads,
    )


@router.get(
    "/{model_id}",
    response_model=ModelRead,
    summary="Get model detail with files and metadata",
)
def get_model(model_id: int, session: Session = Depends(get_session)) -> ModelRead:
    return _build_model_read(session, model_id)


@router.patch(
    "/{model_id}",
    response_model=ModelRead,
    dependencies=[Depends(require_auth)],
    summary="Update a model's name, description, category, or tags",
)
def update_model(
    model_id: int,
    payload: ModelUpdate,
    session: Session = Depends(get_session),
) -> ModelRead:
    m = _live_model(session, model_id)

    if payload.name is not None:
        m.name = payload.name.strip() or m.name
    if payload.description is not None:
        m.description = payload.description

    if payload.category is not None:
        if payload.category.strip() == "":
            m.category_id = None
        else:
            cat = taxonomy.resolve_or_create_category(session, payload.category)
            if cat is not None:
                m.category_id = cat.id

    if payload.tags is not None:
        session.exec(delete(ModelTagLink).where(ModelTagLink.model_id == model_id))  # type: ignore[call-overload]
        if payload.tags:
            new_tags = taxonomy.resolve_or_create_tags(session, payload.tags)
            for t in new_tags:
                session.add(ModelTagLink(model_id=model_id, tag_id=t.id))

    m.updated_at = utcnow()
    session.add(m)
    session.commit()
    return _build_model_read(session, model_id)


@router.patch(
    "/{model_id}/files/{file_id}/revision",
    response_model=ModelRead,
    dependencies=[Depends(require_auth)],
    summary="Update G-code revision status, notes, or recommended marker",
    description=(
        "Updates 1.0 G-code revision fields for a file under a model. Only G-code "
        "files are supported. Marking a file recommended clears the marker from "
        "other G-code files on the same model."
    ),
)
def update_file_revision(
    model_id: int,
    file_id: int,
    payload: FileRevisionUpdate,
    session: Session = Depends(get_session),
) -> ModelRead:
    m = _live_model(session, model_id)
    file_row = session.get(File, file_id)
    if (
        file_row is None
        or file_row.model_id != model_id
        or file_row.deleted_at is not None
    ):
        raise HTTPException(status_code=404, detail="file_not_found")
    if file_row.file_type != FileType.GCODE:
        raise HTTPException(status_code=400, detail="revision_not_supported")

    fields = payload.model_fields_set
    if "revision_status" in fields:
        file_row.revision_status = payload.revision_status
    if "revision_notes" in fields:
        notes = payload.revision_notes
        file_row.revision_notes = notes.strip() if notes and notes.strip() else None
    if "is_recommended" in fields:
        file_row.is_recommended = bool(payload.is_recommended)
        if file_row.is_recommended:
            other_gcode = session.exec(
                select(File).where(
                    File.model_id == model_id,
                    File.id != file_id,
                    File.file_type == FileType.GCODE,
                    File.deleted_at.is_(None),  # type: ignore[union-attr]
                )
            ).all()
            for other in other_gcode:
                other.is_recommended = False
                session.add(other)

    m.updated_at = utcnow()
    session.add(file_row)
    session.add(m)
    session.commit()
    return _build_model_read(session, model_id)


@router.delete(
    "/{model_id}",
    status_code=204,
    response_class=Response,
    dependencies=[Depends(require_auth)],
    summary="Soft-delete a model",
    description=(
        "Marks the model deleted. Files remain on disk; Stage 4 will introduce "
        "hard delete + GC."
    ),
)
def delete_model(model_id: int, session: Session = Depends(get_session)) -> Response:
    m = _live_model(session, model_id)
    m.deleted_at = utcnow()
    session.add(m)
    session.commit()
    return Response(status_code=204)
