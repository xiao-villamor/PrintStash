"""Standalone multipart model compositions.

These endpoints reference existing Models; they never turn a member into a
hidden child or copy its files/revisions.
"""

from __future__ import annotations

import hashlib
from typing import Optional
from uuid import uuid4

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Response,
    UploadFile,
    status,
)
from fastapi import File as UploadFileParam
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, delete, select

from app.core.security import require_auth, require_user
from app.core.time import utcnow
from app.db.models import (
    Collection,
    CollectionRole,
    MultipartModel,
    MultipartModelStar,
    MultipartModelTagLink,
    User,
)
from app.db.scopes import live
from app.db.session import get_session
from app.schemas.models import TagSetUpdate
from app.schemas.multipart_models import (
    MultipartMemberRead,
    MultipartModelCreate,
    MultipartModelListItem,
    MultipartModelRead,
    MultipartModelSave,
    MultipartModelStarRead,
    MultipartModelUpdate,
    MultipartPartsReplace,
)
from app.services import multipart_models, rbac, taxonomy
from app.services.source_cover_processing import (
    MAX_SOURCE_COVER_BYTES,
    SourceCoverProcessingError,
    process_source_cover_upload,
)
from app.services.storage_backend import CreationReceipt, get_backend
from app.services.storage_deletion import (
    enqueue_owned_key,
    process_storage_delete_intents,
)
from app.services.storage_ownership import publish_bytes

router = APIRouter(prefix="/multipart-models", tags=["multipart-models"])


def _uploaded_cover_key(aggregate: MultipartModel) -> str | None:
    if aggregate.id is None or aggregate.cover_filename is None:
        return None
    return get_backend().multipart_model_cover_key(
        int(aggregate.id), aggregate.cover_filename
    )


def _enqueue_uploaded_cover_delete(
    session: Session,
    aggregate: MultipartModel,
    *,
    key: str | None = None,
) -> bool:
    key = key or _uploaded_cover_key(aggregate)
    if key is None:
        return False
    enqueue_owned_key(
        session,
        get_backend(),
        key,
        required_proof=True,
        resource_kind="multipart_model_cover",
        resource_id=aggregate.id,
    )
    aggregate.cover_filename = None
    aggregate.cover_content_type = None
    aggregate.cover_size_bytes = None
    return True


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
    tag: Optional[list[str]] = Query(None),
    favorites: bool = Query(False, description="Only sets starred by current user"),
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
        tag_slugs=tag or [],
        favorites=favorites,
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


@router.put(
    "/{multipart_model_id}/tags",
    response_model=MultipartModelRead,
    dependencies=[Depends(require_auth)],
    summary="Replace a multipart model's direct tags",
)
def replace_multipart_model_tags(
    multipart_model_id: int,
    payload: TagSetUpdate,
    current_user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> MultipartModelRead:
    aggregate = multipart_models.require(
        session, current_user, multipart_model_id, CollectionRole.EDIT
    )
    session.exec(
        delete(MultipartModelTagLink).where(
            MultipartModelTagLink.multipart_model_id == multipart_model_id
        )
    )
    tags = taxonomy.resolve_or_create_tags_in_transaction(session, payload.tags)
    for tag in tags:
        session.add(
            MultipartModelTagLink(
                multipart_model_id=multipart_model_id,
                tag_id=int(tag.id),
            )
        )
    aggregate.updated_at = utcnow()
    aggregate.updated_by = current_user.id
    session.add(aggregate)
    session.commit()
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


@router.get(
    "/{multipart_model_id}/cover/content",
    summary="Serve a multipart model's uploaded cover",
)
def get_multipart_model_cover(
    multipart_model_id: int,
    current_user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> Response:
    aggregate = multipart_models.require(
        session, current_user, multipart_model_id, CollectionRole.VIEW
    )
    key = _uploaded_cover_key(aggregate)
    if key is None:
        raise HTTPException(status_code=404, detail="multipart_cover_not_found")
    backend = get_backend()
    if not backend.exists(key):
        raise HTTPException(status_code=410, detail="multipart_cover_blob_missing")
    return Response(
        content=backend.read_bytes(key),
        media_type=aggregate.cover_content_type or "image/webp",
        headers={
            "Cache-Control": "private, no-cache",
            "ETag": f'"multipart-cover-{aggregate.id}-{aggregate.cover_filename}"',
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.put(
    "/{multipart_model_id}/cover",
    response_model=MultipartModelRead,
    dependencies=[Depends(require_auth)],
    summary="Upload a multipart model cover",
)
async def put_multipart_model_cover(
    multipart_model_id: int,
    file: UploadFile = UploadFileParam(...),
    current_user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> MultipartModelRead:
    aggregate = multipart_models.require(
        session, current_user, multipart_model_id, CollectionRole.EDIT
    )
    data = await file.read(MAX_SOURCE_COVER_BYTES + 1)
    try:
        processed = process_source_cover_upload(data, file.content_type)
    except SourceCoverProcessingError as exc:
        raise HTTPException(status_code=422, detail="multipart_cover_invalid") from exc

    old_cover_key = _uploaded_cover_key(aggregate)
    # The ownership ledger reserves through an independent session. Release
    # this read-only transaction first so SQLite never has two writers waiting
    # on the same connection while the cover publication becomes durable.
    session.commit()
    digest = hashlib.sha256(processed.data).hexdigest()
    filename = f"{digest[:16]}-{uuid4().hex}.webp"
    backend = get_backend()
    key = backend.multipart_model_cover_key(multipart_model_id, filename)
    receipt: CreationReceipt | None = None
    try:
        receipt = publish_bytes(
            session,
            backend,
            key,
            processed.data,
            object_kind="multipart_model_cover",
            sha256=digest,
        )
        if old_cover_key is not None:
            _enqueue_uploaded_cover_delete(session, aggregate, key=old_cover_key)
        aggregate.cover_filename = filename
        aggregate.cover_content_type = processed.content_type
        aggregate.cover_size_bytes = len(processed.data)
        aggregate.cover_image_url = None
        aggregate.updated_by = current_user.id
        aggregate.updated_at = utcnow()
        session.add(aggregate)
        session.commit()
    except Exception:
        session.rollback()
        if receipt is not None:
            backend.rollback_create(receipt)
        raise
    if old_cover_key is not None:
        process_storage_delete_intents()
    session.refresh(aggregate)
    return multipart_models.read(session, current_user, aggregate)


@router.delete(
    "/{multipart_model_id}/cover",
    response_model=MultipartModelRead,
    dependencies=[Depends(require_auth)],
    summary="Remove a multipart model's uploaded cover",
)
def delete_multipart_model_cover(
    multipart_model_id: int,
    current_user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> MultipartModelRead:
    aggregate = multipart_models.require(
        session, current_user, multipart_model_id, CollectionRole.EDIT
    )
    if not _enqueue_uploaded_cover_delete(session, aggregate):
        raise HTTPException(status_code=404, detail="multipart_cover_not_found")
    aggregate.updated_by = current_user.id
    aggregate.updated_at = utcnow()
    session.add(aggregate)
    session.commit()
    process_storage_delete_intents()
    session.refresh(aggregate)
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
    removed_uploaded_cover = False
    if "cover_image_url" in payload.model_fields_set:
        if payload.cover_image_url is not None and aggregate.cover_filename is not None:
            removed_uploaded_cover = _enqueue_uploaded_cover_delete(session, aggregate)
        aggregate.cover_image_url = (
            str(payload.cover_image_url)
            if payload.cover_image_url is not None
            else None
        )
    aggregate.updated_by = current_user.id
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
    result = multipart_models.read(session, current_user, aggregate)
    if removed_uploaded_cover:
        process_storage_delete_intents()
    return result


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
    removed_uploaded_cover = False
    if payload.cover_image_url is not None and aggregate.cover_filename is not None:
        removed_uploaded_cover = _enqueue_uploaded_cover_delete(session, aggregate)
    try:
        result = multipart_models.save(
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
            cover_image_url=(
                str(payload.cover_image_url)
                if payload.cover_image_url is not None
                else None
            ),
            cover_image_set="cover_image_url" in payload.model_fields_set,
        )
        if removed_uploaded_cover:
            process_storage_delete_intents()
        return result
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


@router.put(
    "/{multipart_model_id}/star",
    response_model=MultipartModelStarRead,
    dependencies=[Depends(require_auth)],
    summary="Star a multipart model",
)
def star_multipart_model(
    multipart_model_id: int,
    current_user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> MultipartModelStarRead:
    multipart_models.require(
        session, current_user, multipart_model_id, CollectionRole.VIEW
    )
    existing = session.exec(
        select(MultipartModelStar).where(
            MultipartModelStar.user_id == current_user.id,
            MultipartModelStar.multipart_model_id == multipart_model_id,
        )
    ).first()
    if existing is None:
        session.add(
            MultipartModelStar(
                user_id=int(current_user.id), multipart_model_id=multipart_model_id
            )
        )
        session.commit()
    return MultipartModelStarRead(multipart_model_id=multipart_model_id, starred=True)


@router.delete(
    "/{multipart_model_id}/star",
    response_model=MultipartModelStarRead,
    dependencies=[Depends(require_auth)],
    summary="Unstar a multipart model",
)
def unstar_multipart_model(
    multipart_model_id: int,
    current_user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> MultipartModelStarRead:
    multipart_models.require(
        session, current_user, multipart_model_id, CollectionRole.VIEW
    )
    session.exec(
        delete(MultipartModelStar).where(
            MultipartModelStar.user_id == current_user.id,
            MultipartModelStar.multipart_model_id == multipart_model_id,
        )
    )
    session.commit()
    return MultipartModelStarRead(multipart_model_id=multipart_model_id, starred=False)


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
    removed_uploaded_cover = _enqueue_uploaded_cover_delete(session, aggregate)
    multipart_models.delete_aggregate(session, aggregate)
    if removed_uploaded_cover:
        process_storage_delete_intents()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
