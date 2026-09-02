"""Standalone multipart model compositions.

These endpoints reference existing Models; they never turn a member into a
hidden child or copy its files/revisions.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.core.security import require_auth, require_user
from app.db.models import Collection, CollectionRole, MultipartModel, User
from app.db.scopes import live
from app.db.session import get_session
from app.schemas.multipart_models import (
    MultipartMemberRead,
    MultipartModelCreate,
    MultipartModelListItem,
    MultipartModelRead,
    MultipartModelSave,
    MultipartModelUpdate,
    MultipartPartsReplace,
)
from app.services import multipart_models, rbac, taxonomy

router = APIRouter(prefix="/multipart-models", tags=["multipart-models"])


def _collection_for_write(
    session: Session, user: User, collection_id: int | None
) -> None:
    if collection_id is not None:
        collection = session.exec(
            select(Collection).where(Collection.id == collection_id, live(Collection))
        ).first()
        if collection is None:
            raise HTTPException(status_code=404, detail="collection_not_found")
    rbac.require_collection_role(session, user, collection_id, CollectionRole.EDIT)


def _unique_slug(session: Session, name: str, *, exclude_id: int | None = None) -> str:
    slug = taxonomy.slugify(name)
    existing = session.exec(
        select(MultipartModel).where(MultipartModel.slug == slug)
    ).first()
    if existing is not None and existing.id != exclude_id:
        raise HTTPException(status_code=409, detail="multipart_model_slug_exists")
    return slug


@router.get(
    "", response_model=list[MultipartModelListItem], summary="List multipart models"
)
def list_multipart_models(
    collection: Optional[str] = Query(None),
    direct: bool = Query(False),
    q: Optional[str] = Query(None, max_length=128),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> list[MultipartModelListItem]:
    return multipart_models.list_visible(
        session,
        current_user,
        collection=collection,
        direct=direct,
        query=q,
        limit=limit,
        offset=offset,
    )


@router.post(
    "",
    response_model=MultipartModelRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_auth)],
    summary="Create a multipart model",
)
def create_multipart_model(
    payload: MultipartModelCreate,
    current_user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> MultipartModelRead:
    _collection_for_write(session, current_user, payload.collection_id)
    name = " ".join(payload.name.split())
    if not name:
        raise HTTPException(status_code=422, detail="name_required")
    aggregate = MultipartModel(
        name=name,
        slug=_unique_slug(session, name),
        description=payload.description,
        collection_id=payload.collection_id,
        created_by=current_user.id,
        updated_by=current_user.id,
    )
    session.add(aggregate)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(
            status_code=409, detail="multipart_model_slug_exists"
        ) from exc
    session.refresh(aggregate)
    return multipart_models.read(session, current_user, aggregate)


@router.get(
    "/{multipart_model_id}",
    response_model=MultipartModelRead,
    summary="Get a multipart model",
)
def get_multipart_model(
    multipart_model_id: int,
    current_user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> MultipartModelRead:
    aggregate = multipart_models.require(
        session, current_user, multipart_model_id, CollectionRole.VIEW
    )
    return multipart_models.read(session, current_user, aggregate)


@router.patch(
    "/{multipart_model_id}",
    response_model=MultipartModelRead,
    dependencies=[Depends(require_auth)],
    summary="Update multipart model metadata",
)
def update_multipart_model(
    multipart_model_id: int,
    payload: MultipartModelUpdate,
    current_user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> MultipartModelRead:
    aggregate = multipart_models.require(
        session, current_user, multipart_model_id, CollectionRole.EDIT
    )
    if payload.name is not None:
        name = " ".join(payload.name.split())
        if not name:
            raise HTTPException(status_code=422, detail="name_required")
        if name != aggregate.name:
            aggregate.slug = _unique_slug(session, name, exclude_id=int(aggregate.id))
            aggregate.name = name
    if "description" in payload.model_fields_set:
        aggregate.description = payload.description
    aggregate.updated_by = current_user.id
    from app.core.time import utcnow

    aggregate.updated_at = utcnow()
    session.add(aggregate)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(
            status_code=409, detail="multipart_model_slug_exists"
        ) from exc
    session.refresh(aggregate)
    return multipart_models.read(session, current_user, aggregate)


@router.put(
    "/{multipart_model_id}/parts",
    response_model=MultipartModelRead,
    dependencies=[Depends(require_auth)],
    summary="Replace multipart model composition",
)
def replace_multipart_parts(
    multipart_model_id: int,
    payload: MultipartPartsReplace,
    current_user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> MultipartModelRead:
    aggregate = multipart_models.require(
        session, current_user, multipart_model_id, CollectionRole.EDIT
    )
    try:
        return multipart_models.replace_parts(
            session, current_user, aggregate, payload.parts
        )
    except multipart_models.MultipartModelError as exc:
        session.rollback()
        code = exc.code
        if code == "collection_permission_denied":
            raise HTTPException(status_code=403, detail=code) from exc
        raise HTTPException(status_code=400, detail=code) from exc
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(status_code=400, detail="multipart_parts_invalid") from exc


@router.put(
    "/{multipart_model_id}",
    response_model=MultipartModelRead,
    dependencies=[Depends(require_auth)],
    summary="Atomically update metadata and multipart composition",
)
def save_multipart_model(
    multipart_model_id: int,
    payload: MultipartModelSave,
    current_user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> MultipartModelRead:
    aggregate = multipart_models.require(
        session, current_user, multipart_model_id, CollectionRole.EDIT
    )
    name: str | None = None
    slug: str | None = None
    if payload.name is not None:
        name = " ".join(payload.name.split())
        if not name:
            raise HTTPException(status_code=422, detail="name_required")
        if name != aggregate.name:
            slug = _unique_slug(session, name, exclude_id=int(aggregate.id))
    collection_set = "collection_id" in payload.model_fields_set
    if collection_set:
        _collection_for_write(session, current_user, payload.collection_id)
    try:
        return multipart_models.save(
            session,
            current_user,
            aggregate,
            payload.parts,
            name=name,
            slug=slug,
            description=payload.description,
            description_set="description" in payload.model_fields_set,
            collection_id=payload.collection_id,
            collection_set=collection_set,
            cover_model_id=payload.cover_model_id,
            cover_model_set="cover_model_id" in payload.model_fields_set,
        )
    except multipart_models.MultipartModelError as exc:
        session.rollback()
        code = exc.code
        if code == "collection_permission_denied":
            raise HTTPException(status_code=403, detail=code) from exc
        raise HTTPException(status_code=400, detail=code) from exc
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(status_code=400, detail="multipart_parts_invalid") from exc


@router.get(
    "/{multipart_model_id}/candidates",
    response_model=list[MultipartMemberRead],
    summary="List readable Models for multipart parts",
)
def list_multipart_candidates(
    multipart_model_id: int,
    q: Optional[str] = Query(None, max_length=128),
    limit: int = Query(100, ge=1, le=500),
    current_user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> list[MultipartMemberRead]:
    multipart_models.require(
        session, current_user, multipart_model_id, CollectionRole.VIEW
    )
    return multipart_models.candidates(session, current_user, query=q, limit=limit)


@router.delete(
    "/{multipart_model_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    dependencies=[Depends(require_auth)],
    summary="Delete a multipart model grouping",
)
def delete_multipart_model(
    multipart_model_id: int,
    current_user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> Response:
    aggregate = multipart_models.require(
        session, current_user, multipart_model_id, CollectionRole.EDIT
    )
    multipart_models.delete_aggregate(session, aggregate)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
