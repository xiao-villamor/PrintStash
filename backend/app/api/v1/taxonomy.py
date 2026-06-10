"""Collections (hierarchical) and tags (flat) — browse, create, delete.

List endpoints batch their model counts into a single grouped query; the
single-row helper (`_collection_model_count`) remains for the
create/move/delete paths that only touch one row.
"""

from __future__ import annotations

from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import func
from sqlmodel import Session, select

from app.core.http import get_or_404
from app.core.security import require_auth
from app.core.time import utcnow
from app.db.models import Collection, Model, ModelTagLink, Tag
from app.db.session import get_session
from app.schemas.models import (
    CollectionCreate,
    CollectionMove,
    CollectionRead,
    TagCreate,
    TagRead,
)
from app.services import taxonomy
from app.services.taxonomy import slugify
from app.db.scopes import live

router = APIRouter(tags=["taxonomy"])


# ---------------------------------------------------------------------------
# Collections
# ---------------------------------------------------------------------------


def _collection_model_count(session: Session, path: str) -> int:
    matching_cat_ids = select(Collection.id).where(
        (Collection.path == path) | (Collection.path.startswith(path + "/"))
    )
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
def list_collections(session: Session = Depends(get_session)) -> List[CollectionRead]:
    cats = session.exec(
        select(Collection).where(live(Collection)).order_by(Collection.path)  # type: ignore[union-attr]
    ).all()
    # One grouped query for direct counts; subtree totals aggregate in memory.
    direct_counts: dict[int, int] = dict(
        session.exec(
            select(Model.collection_id, func.count(Model.id))
            .where(live(Model), Model.collection_id.is_not(None))  # type: ignore[union-attr]
            .group_by(Model.collection_id)
        ).all()
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
    session: Session = Depends(get_session),
) -> CollectionRead:
    name = payload.name.strip()
    if "/" in name and payload.parent_id is None:
        collection = taxonomy.resolve_or_create_collection(session, name)
        if collection is None:
            raise HTTPException(status_code=400, detail="collection_name_required")
        return CollectionRead(
            id=collection.id,
            name=collection.name,
            slug=collection.slug,
            path=collection.path,
            parent_id=collection.parent_id,
            model_count=_collection_model_count(session, collection.path),
        )

    slug = slugify(name)
    parent_id = payload.parent_id
    path = slug
    if parent_id is not None:
        parent = get_or_404(session, Collection, payload.parent_id, "parent_not_found")
        path = f"{parent.path}/{slug}"

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
            model_count=_collection_model_count(session, existing.path),
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
    session: Session = Depends(get_session),
) -> CollectionRead:
    col = get_or_404(session, Collection, collection_id, "collection_not_found")

    new_parent_id = payload.parent_id
    if new_parent_id is not None:
        parent = get_or_404(session, Collection, new_parent_id, "parent_not_found")
        if parent.path == col.path or parent.path.startswith(col.path + "/"):
            raise HTTPException(status_code=400, detail="circular_reference")
        new_path = f"{parent.path}/{col.slug}"
    else:
        new_path = col.slug

    if new_path == col.path:
        return CollectionRead(
            id=col.id,
            name=col.name,
            slug=col.slug,
            path=col.path,
            parent_id=col.parent_id,
            model_count=_collection_model_count(session, col.path),
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
        model_count=_collection_model_count(session, col.path),
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
    session: Session = Depends(get_session),
) -> Response:
    cat = get_or_404(session, Collection, collection_id, "collection_not_found")
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
            select(Collection).where(Collection.parent_id == collection_id)
        ).first():
            raise HTTPException(status_code=409, detail="collection_has_children")
        if _collection_model_count(session, cat.path):
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
def list_tags(session: Session = Depends(get_session)) -> List[TagRead]:
    tags = session.exec(
        select(Tag).where(live(Tag)).order_by(Tag.name)  # type: ignore[union-attr]
    ).all()
    counts: dict[int, int] = dict(
        session.exec(
            select(ModelTagLink.tag_id, func.count(ModelTagLink.model_id))
            .join(Model, Model.id == ModelTagLink.model_id)
            .where(live(Model))
            .group_by(ModelTagLink.tag_id)
        ).all()
    )
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
    session: Session = Depends(get_session),
) -> Response:
    from sqlmodel import delete

    tag = get_or_404(session, Tag, tag_id, "tag_not_found")
    session.exec(delete(ModelTagLink).where(ModelTagLink.tag_id == tag_id))  # type: ignore[call-overload]
    tag.deleted_at = utcnow()
    session.add(tag)
    session.commit()
    return Response(status_code=204)
