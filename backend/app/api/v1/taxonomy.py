"""Collections (hierarchical) and tags (flat) — browse, create, delete.

List endpoints batch their model counts into a single grouped query; the
single-row helper (`_collection_model_count`) remains for the
create/move/delete paths that only touch one row.
"""

from __future__ import annotations

import hashlib
import re
from typing import List, Optional

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Response,
    UploadFile,
    status,
)
from fastapi import (
    File as FileParam,
)
from printstash_core.files import slugify
from sqlalchemy import func
from sqlalchemy import select as sa_select
from sqlmodel import Session, delete, select

from app.api.artifact_responses import serve_stored_file
from app.core.config import settings
from app.core.http import get_or_404
from app.core.security import require_auth, require_user
from app.core.time import utcnow
from app.db.models import (
    Collection,
    CollectionPermission,
    CollectionRole,
    CollectionTagLink,
    FileTagLink,
    Model,
    ModelTagLink,
    MultipartModel,
    MultipartModelTagLink,
    Tag,
    User,
)
from app.db.projections import content_changed
from app.db.scopes import live
from app.db.session import get_session
from app.modules.identity import rbac
from app.modules.library import collection_tree, library_search, taxonomy, trash
from app.modules.storage.storage_backend.contracts import StorageCollisionError
from app.modules.storage.storage_backend.runtime import get_backend
from app.modules.storage.storage_ownership import publish_bytes
from app.schemas.models import (
    CollectionCreate,
    CollectionImageUpload,
    CollectionLookupRead,
    CollectionMove,
    CollectionPage,
    CollectionPermissionRead,
    CollectionPermissionUpdate,
    CollectionRead,
    CollectionReadmeRead,
    CollectionReadmeUpdate,
    TagCreate,
    TagRead,
    TagSetUpdate,
)

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


def _collection_read(
    session: Session,
    current_user: User,
    collection: Collection,
    *,
    model_count: int,
) -> CollectionRead:
    tags = collection_tree.collection_tags(
        session, [collection.id] if collection.id is not None else []
    )
    return CollectionRead(
        id=collection.id,  # type: ignore[arg-type]
        name=collection.name,
        slug=collection.slug,
        path=collection.path,
        parent_id=collection.parent_id,
        model_count=model_count,
        effective_role=rbac.effective_collection_role(
            session, current_user, collection.id
        ),
        tags=tags.get(collection.id or 0, []),
        has_readme=bool(collection.readme),
    )


# ---------------------------------------------------------------------------
# Collections
# ---------------------------------------------------------------------------


def _collection_model_count(session: Session, path: str, user: User) -> int:
    matching_cat_ids = select(Collection.id).where(
        (Collection.path == path) | (Collection.path.startswith(path + "/"))
    )
    if not user.is_superuser:
        matching_cat_ids = matching_cat_ids.where(
            Collection.id.in_(rbac.accessible_collection_ids_stmt(session, user))  # type: ignore[union-attr]
        )
    count = session.exec(
        select(func.count(Model.id)).where(
            live(Model),
            Model.collection_id.in_(matching_cat_ids),
        )
    ).one()
    return int(count or 0)


@router.get(
    "/collections/children",
    response_model=CollectionPage,
    summary="Page through a collection's children, or the caller's root collections",
)
def list_collection_children(
    parent_id: Optional[int] = Query(None),
    cursor: Optional[str] = Query(None),
    limit: int = Query(200, ge=1, le=collection_tree.CHILDREN_LIMIT_MAX),
    current_user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> CollectionPage:
    return collection_tree.children(
        session, current_user, parent_id=parent_id, cursor=cursor, limit=limit
    )


@router.get(
    "/collections/lookup",
    response_model=CollectionLookupRead,
    summary="Find a collection by path or id, with its visible ancestors",
)
def lookup_collection(
    path: Optional[str] = Query(None, min_length=1, max_length=512),
    collection_id: Optional[int] = Query(None, alias="id", ge=1),
    current_user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> CollectionLookupRead:
    if path is not None and collection_id is not None:
        raise HTTPException(
            status_code=422, detail="collection_lookup_requires_path_or_id"
        )
    if path is not None:
        return collection_tree.lookup(session, current_user, path)
    if collection_id is not None:
        return collection_tree.lookup_by_id(session, current_user, collection_id)
    raise HTTPException(status_code=422, detail="collection_lookup_requires_path_or_id")


@router.get(
    "/collections/search",
    response_model=CollectionPage,
    summary="Search collections by name, at a minimum role",
)
def search_collections(
    q: str = Query("", max_length=128),
    min_role: CollectionRole = Query(CollectionRole.VIEW),
    cursor: Optional[str] = Query(None),
    limit: int = Query(20, ge=1, le=collection_tree.SEARCH_LIMIT_MAX),
    current_user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> CollectionPage:
    return collection_tree.search(
        session,
        current_user,
        query=q,
        minimum=min_role,
        cursor=cursor,
        limit=limit,
    )


@router.get(
    "/collections",
    response_model=List[CollectionRead],
    summary="List all collections with model counts",
    description=(
        "Returns the whole tree. Deprecated: use /collections/children, "
        "/collections/lookup and /collections/search; removal is planned for 0.16."
    ),
    deprecated=True,
)
def list_collections(
    current_user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> List[CollectionRead]:
    # A fixed number of statements, none binding a parameter per collection:
    # the visible set is one subquery every other read filters by (#295).
    visible = rbac.accessible_collection_ids_stmt(session, current_user)
    rows = session.execute(
        sa_select(
            Collection.id,
            Collection.name,
            Collection.slug,
            Collection.path,
            Collection.parent_id,
            # Only whether a readme exists; the text can be large.
            func.coalesce(func.length(Collection.readme), 0) > 0,
        )
        .where(Collection.id.in_(visible))  # type: ignore[union-attr]
        .order_by(Collection.path)
    ).all()
    direct_counts: dict[int, int] = dict(
        session.exec(
            select(Model.collection_id, func.count(Model.id))
            .where(live(Model), Model.collection_id.in_(visible))  # type: ignore[union-attr]
            .group_by(Model.collection_id)
        ).all()
    )
    totals = taxonomy.subtree_totals(
        {path: direct_counts.get(cid, 0) for cid, _, _, path, _, _ in rows}
    )
    roles = rbac.effective_roles_for_paths(
        session, current_user, ((cid, path) for cid, _, _, path, _, _ in rows)
    )
    tags_by_collection = collection_tree.collection_tags(session, visible)
    return [
        CollectionRead(
            id=cid,
            name=name,
            slug=slug,
            path=path,
            parent_id=parent_id,
            model_count=totals[path],
            effective_role=roles[cid],
            tags=tags_by_collection.get(cid, []),
            has_readme=has_readme,
        )
        for cid, name, slug, path, parent_id, has_readme in rows
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
        return _collection_read(
            session,
            current_user,
            collection,
            model_count=_collection_model_count(session, collection.path, current_user),
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
        content_changed(session, "collection", [existing.id])
        session.commit()
        session.refresh(existing)
        return _collection_read(
            session,
            current_user,
            existing,
            model_count=_collection_model_count(session, existing.path, current_user),
        )

    collection = Collection(name=name, slug=slug, parent_id=parent_id, path=path)
    session.add(collection)
    content_changed(session, "collection", (row.id for row in (collection,)))
    session.commit()
    session.refresh(collection)
    return _collection_read(session, current_user, collection, model_count=0)


@router.patch(
    "/collections/{collection_id}",
    response_model=CollectionRead,
    dependencies=[Depends(require_auth)],
    summary="Move or rename a collection",
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

    moving = (
        "parent_id" in payload.model_fields_set and payload.parent_id != col.parent_id
    )
    new_parent_id = (
        payload.parent_id if "parent_id" in payload.model_fields_set else col.parent_id
    )
    new_name = payload.name.strip() if payload.name is not None else col.name
    if not new_name:
        raise HTTPException(status_code=422, detail="collection_name_required")
    new_slug = slugify(new_name)
    if new_parent_id is not None:
        parent = get_or_404(session, Collection, new_parent_id, "parent_not_found")
        if moving:
            rbac.require_collection_role(
                session, current_user, parent.id, CollectionRole.ADMIN
            )
        if parent.path == col.path or parent.path.startswith(col.path + "/"):
            raise HTTPException(status_code=400, detail="circular_reference")
        new_path = f"{parent.path}/{new_slug}"
    else:
        if moving and not current_user.is_superuser:
            raise HTTPException(
                status_code=403, detail="root_collection_admin_required"
            )
        new_path = new_slug

    if new_path == col.path and new_name == col.name:
        return _collection_read(
            session,
            current_user,
            col,
            model_count=_collection_model_count(session, col.path, current_user),
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
    col.name = new_name
    col.slug = new_slug
    col.path = new_path
    session.add(col)
    content_changed(session, "collection", [col.id])
    session.commit()
    session.refresh(col)
    return _collection_read(
        session,
        current_user,
        col,
        model_count=_collection_model_count(session, col.path, current_user),
    )


@router.put(
    "/collections/{collection_id}/tags",
    response_model=CollectionRead,
    dependencies=[Depends(require_auth)],
    summary="Replace a collection's direct tags",
)
def replace_collection_tags(
    collection_id: int,
    payload: TagSetUpdate,
    current_user: User = Depends(require_user),
    _: None = Depends(require_auth),
    session: Session = Depends(get_session),
) -> CollectionRead:
    collection = get_or_404(session, Collection, collection_id, "collection_not_found")
    if collection.deleted_at is not None:
        raise HTTPException(status_code=404, detail="collection_not_found")
    rbac.require_collection_role(
        session, current_user, collection.id, CollectionRole.EDIT
    )

    session.exec(
        delete(CollectionTagLink).where(
            CollectionTagLink.collection_id == collection_id
        )
    )
    tags = taxonomy.resolve_or_create_tags_in_transaction(session, payload.tags)
    for tag in tags:
        session.add(CollectionTagLink(collection_id=collection_id, tag_id=tag.id))
    content_changed(session, "collection", [collection_id])
    session.commit()
    return _collection_read(
        session,
        current_user,
        collection,
        model_count=_collection_model_count(session, collection.path, current_user),
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
    content_changed(session, "collection", [col.id])
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

    ext = (
        ("." + (file.filename or "").rsplit(".", 1)[-1].lower())
        if "." in (file.filename or "")
        else ""
    )
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
    receipt = None
    try:
        receipt = publish_bytes(
            session,
            backend,
            key,
            data,
            object_kind="collection_image",
        )
        session.commit()
    except StorageCollisionError as exc:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="storage_destination_exists",
        ) from exc
    except Exception:
        session.rollback()
        if receipt is not None:
            backend.rollback_create(receipt)
        raise
    return CollectionImageUpload(url=f"/api/v1/collections/{col.id}/images/{name}")


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
    return serve_stored_file(
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
        # Multipart Models are independent rows that reference their
        # collection.  Recursive deletion must fail closed just like the
        # non-recursive path: otherwise the collection subtree is trashed
        # first and the dangling composition only fails later at purge/FK
        # handling.  This check deliberately precedes every mutation below so
        # a conflict leaves collections and Models untouched.
        multipart_collection_ids = select(Collection.id).where(
            (Collection.path == cat.path) | Collection.path.startswith(cat.path + "/")
        )
        if session.exec(
            select(func.count(MultipartModel.id)).where(
                MultipartModel.collection_id.in_(multipart_collection_ids)  # type: ignore[union-attr]
            )
        ).one():
            raise HTTPException(status_code=409, detail="collection_has_models")
        for desc in descendants:
            desc.deleted_at = now
            session.add(desc)
        # Models in the deleted tree go to the trash, detached from their
        # (now-deleted) collection so restoring lands them in All Models.
        models_in_tree = session.exec(
            select(Model).where(live(Model), Model.collection_id.in_(affected_ids))
        ).all()
        # Use the shared trash seam so external-library files receive durable
        # discovery tombstones and cannot reappear on the next scan.
        trash.soft_delete_models(session, models_in_tree)
        for m in models_in_tree:
            m.collection_id = None
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
        multipart_ids = select(Collection.id).where(
            (Collection.path == cat.path) | Collection.path.startswith(cat.path + "/")
        )
        if session.exec(
            select(func.count(MultipartModel.id)).where(
                MultipartModel.collection_id.in_(multipart_ids)  # type: ignore[union-attr]
            )
        ).one():
            raise HTTPException(status_code=409, detail="collection_has_models")

    cat.deleted_at = now
    session.add(cat)
    content_changed(session, "collection", [cat.id])
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
    if current_user.is_superuser:
        visible = None
        accessible_multipart_ids = select(MultipartModel.id)
    else:
        # A subquery, never the id set bound one parameter per collection.
        visible = rbac.accessible_collection_ids_stmt(session, current_user)
        accessible_multipart_ids = select(MultipartModel.id).where(
            MultipartModel.collection_id.in_(visible)  # type: ignore[union-attr]
        )
    counts = dict(
        session.exec(library_search.accessible_tag_counts_stmt(visible)).all()
    )
    multipart_counts = dict(
        session.exec(
            select(
                MultipartModelTagLink.tag_id,
                func.count(func.distinct(MultipartModelTagLink.multipart_model_id)),
            )
            .where(
                MultipartModelTagLink.multipart_model_id.in_(accessible_multipart_ids)
            )  # type: ignore[union-attr]
            .group_by(MultipartModelTagLink.tag_id)
        ).all()
    )
    return [
        TagRead(
            id=t.id,
            name=t.name,
            slug=t.slug,
            model_count=counts.get(t.id, 0),
            multipart_model_count=multipart_counts.get(t.id, 0),
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
    tag = get_or_404(session, Tag, tag_id, "tag_not_found")
    session.exec(delete(ModelTagLink).where(ModelTagLink.tag_id == tag_id))  # type: ignore[call-overload]
    session.exec(delete(CollectionTagLink).where(CollectionTagLink.tag_id == tag_id))
    session.exec(delete(FileTagLink).where(FileTagLink.tag_id == tag_id))
    session.exec(
        delete(MultipartModelTagLink).where(MultipartModelTagLink.tag_id == tag_id)
    )
    tag.deleted_at = utcnow()
    session.add(tag)
    content_changed(session, "tag", [tag_id])
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
