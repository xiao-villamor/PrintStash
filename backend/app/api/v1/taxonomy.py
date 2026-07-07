"""Collections (hierarchical) and tags (flat) — browse, create, delete.

List endpoints batch their model counts into a single grouped query; the
single-row helper (`_collection_model_count`) remains for the
create/move/delete paths that only touch one row.
"""

from __future__ import annotations

import hashlib
import re
from typing import List

from fastapi import (
    APIRouter,
    Depends,
    File as FileParam,
    HTTPException,
    Query,
    Response,
    UploadFile,
    status,
)
from sqlalchemy import func
from sqlmodel import Session, select

from app.api.v1.files import _serve_file
from app.core.config import settings
from app.core.http import get_or_404
from app.core.security import require_auth, require_user
from app.core.time import utcnow
from app.db.models import (
    Collection,
    CollectionPermission,
    CollectionRole,
    Model,
    ModelTagLink,
    Tag,
    User,
)
from app.db.session import get_session
from app.schemas.models import (
    CollectionCreate,
    CollectionImageUpload,
    CollectionMove,
    CollectionPermissionRead,
    CollectionPermissionUpdate,
    CollectionRead,
    CollectionReadmeRead,
    CollectionReadmeUpdate,
    TagCreate,
    TagRead,
)
from app.services import rbac
from app.services import taxonomy
from app.services.storage_backend import get_backend
from app.services.taxonomy import slugify
from app.db.scopes import live

# Raster image formats only — no SVG (script-capable) — keeps readme images
# safe to serve inline. Maps extension -> media type.
_IMAGE_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}
# Server-generated image names are sha256 + a whitelisted extension; this guards
# the serve path against traversal / arbitrary key reads.
_IMAGE_NAME_RE = re.compile(r"^[0-9a-f]{64}\.(png|jpe?g|gif|webp)$")

router = APIRouter(tags=["taxonomy"])


# ---------------------------------------------------------------------------
# Collections
# ---------------------------------------------------------------------------


def _collection_model_count(session: Session, path: str, user: User) -> int:
    matching_cat_ids = select(Collection.id).where(
        (Collection.path == path) | (Collection.path.startswith(path + "/"))
    )
    if not user.is_superuser:
        accessible_ids = rbac.accessible_collection_ids(session, user)
        matching_cat_ids = matching_cat_ids.where(Collection.id.in_(accessible_ids))  # type: ignore[union-attr]
    count = session.exec(
        select(func.count(Model.id)).where(
            live(Model),
            Model.collection_id.in_(matching_cat_ids),
        )
    ).one()
    return int(count or 0)


@router.get(
    "/collections",
    response_model=List[CollectionRead],
    summary="List all collections with model counts",
)
def list_collections(
    current_user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> List[CollectionRead]:
    stmt = select(Collection).where(live(Collection)).order_by(Collection.path)  # type: ignore[union-attr]
    if not current_user.is_superuser:
        accessible_ids = rbac.accessible_collection_ids(session, current_user)
        if not accessible_ids:
            return []
        stmt = stmt.where(Collection.id.in_(accessible_ids))  # type: ignore[union-attr]
    cats = session.exec(stmt).all()
    # One grouped query for direct counts; subtree totals aggregate in memory.
    model_count_stmt = select(Model.collection_id, func.count(Model.id)).where(
        live(Model),
        Model.collection_id.is_not(None),  # type: ignore[union-attr]
    )
    if not current_user.is_superuser:
        model_count_stmt = model_count_stmt.where(
            Model.collection_id.in_(accessible_ids)
        )  # type: ignore[union-attr]
    direct_counts: dict[int, int] = dict(
        session.exec(model_count_stmt.group_by(Model.collection_id)).all()
    )
    count_by_path = {c.path: direct_counts.get(c.id, 0) for c in cats if c.id}
    return [
        CollectionRead(
            id=c.id,
            name=c.name,
            slug=c.slug,
            path=c.path,
            parent_id=c.parent_id,
            model_count=sum(
                n
                for path, n in count_by_path.items()
                if path == c.path or path.startswith(c.path + "/")
            ),
            effective_role=rbac.effective_collection_role(session, current_user, c.id),
        )
        for c in cats
    ]


@router.post(
    "/collections",
    response_model=CollectionRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_auth)],
    summary="Create a new collection",
)
def create_collection(
    payload: CollectionCreate,
    current_user: User = Depends(require_user),
    _: None = Depends(require_auth),
    session: Session = Depends(get_session),
) -> CollectionRead:
    name = payload.name.strip()
    if "/" in name and payload.parent_id is None:
        if not current_user.is_superuser:
            raise HTTPException(
                status_code=403, detail="root_collection_admin_required"
            )
        collection = taxonomy.resolve_or_create_collection(session, name)
        if collection is None:
            raise HTTPException(status_code=400, detail="collection_name_required")
        return CollectionRead(
            id=collection.id,
            name=collection.name,
            slug=collection.slug,
            path=collection.path,
            parent_id=collection.parent_id,
            model_count=_collection_model_count(session, collection.path, current_user),
            effective_role=rbac.effective_collection_role(
                session, current_user, collection.id
            ),
        )

    slug = slugify(name)
    parent_id = payload.parent_id
    path = slug
    if parent_id is not None:
        parent = get_or_404(session, Collection, payload.parent_id, "parent_not_found")
        rbac.require_collection_role(
            session, current_user, parent.id, CollectionRole.ADMIN
        )
        path = f"{parent.path}/{slug}"
    elif not current_user.is_superuser:
        raise HTTPException(status_code=403, detail="root_collection_admin_required")

    existing = session.exec(select(Collection).where(Collection.path == path)).first()
    if existing is not None:
        if existing.deleted_at is None:
            raise HTTPException(status_code=409, detail="collection_already_exists")
        # Revive a previously-trashed collection sitting at this path instead of
        # creating a duplicate-path row.
        existing.deleted_at = None
        existing.deleted_by = None
        existing.name = name
        existing.parent_id = parent_id
        session.add(existing)
        session.commit()
        session.refresh(existing)
        return CollectionRead(
            id=existing.id,
            name=existing.name,
            slug=existing.slug,
            path=existing.path,
            parent_id=existing.parent_id,
            model_count=_collection_model_count(session, existing.path, current_user),
            effective_role=rbac.effective_collection_role(
                session, current_user, existing.id
            ),
        )

    collection = Collection(name=name, slug=slug, parent_id=parent_id, path=path)
    session.add(collection)
    session.commit()
    session.refresh(collection)
    return CollectionRead(
        id=collection.id,
        name=collection.name,
        slug=collection.slug,
        path=collection.path,
        parent_id=collection.parent_id,
        model_count=0,
        effective_role=rbac.effective_collection_role(
            session, current_user, collection.id
        ),
    )


@router.patch(
    "/collections/{collection_id}",
    response_model=CollectionRead,
    dependencies=[Depends(require_auth)],
    summary="Move a collection to a different parent",
)
def move_collection(
    collection_id: int,
    payload: CollectionMove,
    current_user: User = Depends(require_user),
    _: None = Depends(require_auth),
    session: Session = Depends(get_session),
) -> CollectionRead:
    col = get_or_404(session, Collection, collection_id, "collection_not_found")
    rbac.require_collection_role(session, current_user, col.id, CollectionRole.ADMIN)

    new_parent_id = payload.parent_id
    if new_parent_id is not None:
        parent = get_or_404(session, Collection, new_parent_id, "parent_not_found")
        rbac.require_collection_role(
            session, current_user, parent.id, CollectionRole.ADMIN
        )
        if parent.path == col.path or parent.path.startswith(col.path + "/"):
            raise HTTPException(status_code=400, detail="circular_reference")
        new_path = f"{parent.path}/{col.slug}"
    else:
        if not current_user.is_superuser:
            raise HTTPException(
                status_code=403, detail="root_collection_admin_required"
            )
        new_path = col.slug

    if new_path == col.path:
        return CollectionRead(
            id=col.id,
            name=col.name,
            slug=col.slug,
            path=col.path,
            parent_id=col.parent_id,
            model_count=_collection_model_count(session, col.path, current_user),
            effective_role=rbac.effective_collection_role(
                session, current_user, col.id
            ),
        )

    if session.exec(
        select(Collection).where(
            Collection.path == new_path,
            Collection.id != collection_id,
            live(Collection),
        )
    ).first():
        raise HTTPException(status_code=409, detail="collection_already_exists")

    old_prefix = col.path
    descendants = session.exec(
        select(Collection).where(Collection.path.startswith(old_prefix + "/"))  # type: ignore[union-attr]
    ).all()
    for desc in descendants:
        desc.path = new_path + desc.path[len(old_prefix) :]
        session.add(desc)

    col.parent_id = new_parent_id
    col.path = new_path
    session.add(col)
    session.commit()
    session.refresh(col)
    return CollectionRead(
        id=col.id,
        name=col.name,
        slug=col.slug,
        path=col.path,
        parent_id=col.parent_id,
        model_count=_collection_model_count(session, col.path, current_user),
        effective_role=rbac.effective_collection_role(session, current_user, col.id),
    )


@router.get(
    "/collections/{collection_id}/readme",
    response_model=CollectionReadmeRead,
    summary="Get a collection's markdown landing page",
)
def get_collection_readme(
    collection_id: int,
    current_user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> CollectionReadmeRead:
    col = get_or_404(session, Collection, collection_id, "collection_not_found")
    rbac.require_collection_role(session, current_user, col.id, CollectionRole.VIEW)
    return CollectionReadmeRead(readme=col.readme)


@router.put(
    "/collections/{collection_id}/readme",
    response_model=CollectionReadmeRead,
    dependencies=[Depends(require_auth)],
    summary="Set a collection's markdown landing page",
)
def set_collection_readme(
    collection_id: int,
    payload: CollectionReadmeUpdate,
    current_user: User = Depends(require_user),
    _: None = Depends(require_auth),
    session: Session = Depends(get_session),
) -> CollectionReadmeRead:
    col = get_or_404(session, Collection, collection_id, "collection_not_found")
    rbac.require_collection_role(session, current_user, col.id, CollectionRole.EDIT)
    readme = payload.readme or None  # store empty as NULL
    col.readme = readme
    col.updated_by = current_user.id
    session.add(col)
    session.commit()
    return CollectionReadmeRead(readme=readme)


@router.post(
    "/collections/{collection_id}/images",
    response_model=CollectionImageUpload,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_auth)],
    summary="Upload an image for a collection's readme",
)
async def upload_collection_image(
    collection_id: int,
    file: UploadFile = FileParam(..., description="A PNG/JPEG/GIF/WebP image"),
    current_user: User = Depends(require_user),
    _: None = Depends(require_auth),
    session: Session = Depends(get_session),
) -> CollectionImageUpload:
    col = get_or_404(session, Collection, collection_id, "collection_not_found")
    rbac.require_collection_role(session, current_user, col.id, CollectionRole.EDIT)

    ext = ("." + (file.filename or "").rsplit(".", 1)[-1].lower()) if "." in (
        file.filename or ""
    ) else ""
    media_type = _IMAGE_TYPES.get(ext)
    if media_type is None:
        raise HTTPException(status_code=400, detail="unsupported_image_type")

    # Readme images are read fully into memory to hash — bound that with a 10MB
    # cap (well under the model-upload cap). Check the declared size first so an
    # oversized upload is rejected before it's buffered.
    image_cap = min(10 * 1024 * 1024, settings.max_upload_bytes)
    if file.size is not None and file.size > image_cap:
        raise HTTPException(status_code=413, detail="upload_too_large")
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="empty_file")
    if len(data) > image_cap:
        raise HTTPException(status_code=413, detail="upload_too_large")

    name = f"{hashlib.sha256(data).hexdigest()}{'.jpg' if ext == '.jpeg' else ext}"
    backend = get_backend()
    key = backend.collection_image_key(col.id, name)
    if not backend.exists(key):
        backend.write_bytes(data, key)
    # ponytail: orphaned image blobs aren't reclaimed when a readme drops the ref
    # or the collection is deleted. Add a sweep keyed on collection_image_key
    # prefixes if storage growth becomes a problem.
    return CollectionImageUpload(
        url=f"/api/v1/collections/{col.id}/images/{name}"
    )


@router.get(
    "/collections/{collection_id}/images/{name}",
    summary="Serve an image embedded in a collection's readme",
)
def get_collection_image(
    collection_id: int,
    name: str,
    current_user: User = Depends(require_user),
    session: Session = Depends(get_session),
):
    if not _IMAGE_NAME_RE.match(name):
        raise HTTPException(status_code=404, detail="image_not_found")
    col = get_or_404(session, Collection, collection_id, "collection_not_found")
    rbac.require_collection_role(session, current_user, col.id, CollectionRole.VIEW)
    backend = get_backend()
    key = backend.collection_image_key(col.id, name)
    if not backend.exists(key):
        raise HTTPException(status_code=404, detail="image_not_found")
    media_type = _IMAGE_TYPES[f".{name.rsplit('.', 1)[-1]}"]
    return _serve_file(
        key,
        name,
        media_type,
        headers={
            "Cache-Control": "public, max-age=3600",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.delete(
    "/collections/{collection_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    dependencies=[Depends(require_auth)],
    summary="Delete a collection",
)
def delete_collection(
    collection_id: int,
    recursive: bool = Query(False),
    current_user: User = Depends(require_user),
    _: None = Depends(require_auth),
    session: Session = Depends(get_session),
) -> Response:
    cat = get_or_404(session, Collection, collection_id, "collection_not_found")
    rbac.require_collection_role(session, current_user, cat.id, CollectionRole.ADMIN)
    now = utcnow()

    if recursive:
        descendants = session.exec(
            select(Collection).where(
                Collection.path.startswith(cat.path + "/"),  # type: ignore[union-attr]
                live(Collection),
            )
        ).all()
        affected_ids = [cat.id] + [d.id for d in descendants]
        for desc in descendants:
            desc.deleted_at = now
            session.add(desc)
        # Models in the deleted tree go to the trash, detached from their
        # (now-deleted) collection so restoring lands them in All Models.
        models_in_tree = session.exec(
            select(Model).where(live(Model), Model.collection_id.in_(affected_ids))
        ).all()
        for m in models_in_tree:
            m.collection_id = None
            m.deleted_at = now
            m.updated_at = now
            session.add(m)
    else:
        if session.exec(
            select(Collection).where(
                Collection.parent_id == collection_id,
                live(Collection),  # soft-deleted children must not block the parent
            )
        ).first():
            raise HTTPException(status_code=409, detail="collection_has_children")
        if _collection_model_count(session, cat.path, current_user):
            raise HTTPException(status_code=409, detail="collection_has_models")

    cat.deleted_at = now
    session.add(cat)
    session.commit()
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# Tags
# ---------------------------------------------------------------------------


@router.get(
    "/tags",
    response_model=List[TagRead],
    summary="List all tags with model counts",
)
def list_tags(
    current_user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> List[TagRead]:
    tags = session.exec(
        select(Tag).where(live(Tag)).order_by(Tag.name)  # type: ignore[union-attr]
    ).all()
    count_stmt = (
        select(ModelTagLink.tag_id, func.count(ModelTagLink.model_id))
        .join(Model, Model.id == ModelTagLink.model_id)
        .where(live(Model))
    )
    if not current_user.is_superuser:
        accessible_ids = rbac.accessible_collection_ids(session, current_user)
        if not accessible_ids:
            counts = {}
        else:
            count_stmt = count_stmt.where(Model.collection_id.in_(accessible_ids))  # type: ignore[union-attr]
            counts = dict(session.exec(count_stmt.group_by(ModelTagLink.tag_id)).all())
    else:
        counts = dict(session.exec(count_stmt.group_by(ModelTagLink.tag_id)).all())
    return [
        TagRead(
            id=t.id,
            name=t.name,
            slug=t.slug,
            model_count=counts.get(t.id, 0),
        )
        for t in tags
    ]


@router.post(
    "/tags",
    response_model=TagRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_auth)],
    summary="Create a new tag",
)
def create_tag(
    payload: TagCreate,
    _: None = Depends(require_auth),
    session: Session = Depends(get_session),
) -> TagRead:
    slug = slugify(payload.name)
    if session.exec(select(Tag).where(Tag.slug == slug)).first():
        raise HTTPException(status_code=409, detail="tag_already_exists")

    tag = Tag(name=payload.name, slug=slug)
    session.add(tag)
    session.commit()
    session.refresh(tag)
    return TagRead(id=tag.id, name=tag.name, slug=tag.slug, model_count=0)


@router.delete(
    "/tags/{tag_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    dependencies=[Depends(require_auth)],
    summary="Delete a tag",
)
def delete_tag(
    tag_id: int,
    _: None = Depends(require_auth),
    session: Session = Depends(get_session),
) -> Response:
    from sqlmodel import delete

    tag = get_or_404(session, Tag, tag_id, "tag_not_found")
    session.exec(delete(ModelTagLink).where(ModelTagLink.tag_id == tag_id))  # type: ignore[call-overload]
    tag.deleted_at = utcnow()
    session.add(tag)
    session.commit()
    return Response(status_code=204)


@router.get(
    "/collections/{collection_id}/permissions",
    response_model=List[CollectionPermissionRead],
    summary="List direct permissions for a collection",
)
def list_collection_permissions(
    collection_id: int,
    current_user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> List[CollectionPermissionRead]:
    collection = get_or_404(session, Collection, collection_id, "collection_not_found")
    rbac.require_collection_role(
        session, current_user, collection.id, CollectionRole.ADMIN
    )
    rows = session.exec(
        select(CollectionPermission, User)
        .join(User, User.id == CollectionPermission.user_id)
        .where(CollectionPermission.collection_id == collection_id)
        .order_by(User.username)
    ).all()
    return [
        CollectionPermissionRead(
            user_id=user.id,  # type: ignore[arg-type]
            username=user.username,
            collection_id=permission.collection_id,
            role=permission.role,
            inherited=False,
        )
        for permission, user in rows
    ]


@router.put(
    "/collections/{collection_id}/permissions/{user_id}",
    response_model=CollectionPermissionRead,
    summary="Grant or update a collection permission",
)
def upsert_collection_permission(
    collection_id: int,
    user_id: int,
    payload: CollectionPermissionUpdate,
    current_user: User = Depends(require_user),
    _: None = Depends(require_auth),
    session: Session = Depends(get_session),
) -> CollectionPermissionRead:
    collection = get_or_404(session, Collection, collection_id, "collection_not_found")
    rbac.require_collection_role(
        session, current_user, collection.id, CollectionRole.ADMIN
    )
    target_user = get_or_404(session, User, user_id, "user_not_found")
    permission = session.exec(
        select(CollectionPermission).where(
            CollectionPermission.collection_id == collection_id,
            CollectionPermission.user_id == user_id,
        )
    ).first()
    if permission is None:
        permission = CollectionPermission(
            collection_id=collection_id,
            user_id=user_id,
            role=payload.role,
        )
    else:
        permission.role = payload.role
        permission.updated_at = utcnow()
    session.add(permission)
    session.commit()
    return CollectionPermissionRead(
        user_id=target_user.id,  # type: ignore[arg-type]
        username=target_user.username,
        collection_id=collection_id,
        role=permission.role,
        inherited=False,
    )


@router.delete(
    "/collections/{collection_id}/permissions/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="Remove a direct collection permission",
)
def delete_collection_permission(
    collection_id: int,
    user_id: int,
    current_user: User = Depends(require_user),
    _: None = Depends(require_auth),
    session: Session = Depends(get_session),
) -> Response:
    collection = get_or_404(session, Collection, collection_id, "collection_not_found")
    rbac.require_collection_role(
        session, current_user, collection.id, CollectionRole.ADMIN
    )
    permission = session.exec(
        select(CollectionPermission).where(
            CollectionPermission.collection_id == collection_id,
            CollectionPermission.user_id == user_id,
        )
    ).first()
    if permission is None:
        raise HTTPException(status_code=404, detail="permission_not_found")
    session.delete(permission)
    session.commit()
    return Response(status_code=204)
