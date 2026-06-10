"""Collections (hierarchical) and tags (flat) — browse, create, delete.

The model-count queries are intentionally per-row (N+1) here; with the
hundreds-of-rows scale this endpoint deals with, the simpler code wins.
Stage 4 may switch to a single aggregate JOIN if that ever changes.
"""

from __future__ import annotations

from typing import List

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import func
from sqlmodel import Session, select

from app.core.http import get_or_404
from app.core.security import require_auth
from app.core.time import utcnow
from app.db.models import Collection, Model, ModelTagLink, Tag
from app.db.session import get_session
from app.schemas.models import CollectionCreate, CollectionRead, TagCreate, TagRead
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
    return [
        CollectionRead(
            id=c.id,
            name=c.name,
            slug=c.slug,
            path=c.path,
            parent_id=c.parent_id,
            model_count=_collection_model_count(session, c.path),
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

    if session.exec(select(Collection).where(Collection.path == path)).first():
        raise HTTPException(status_code=409, detail="collection_already_exists")

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


@router.delete(
    "/collections/{collection_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    dependencies=[Depends(require_auth)],
    summary="Delete a collection",
)
def delete_collection(
    collection_id: int,
    session: Session = Depends(get_session),
) -> Response:
    cat = get_or_404(session, Collection, collection_id, "collection_not_found")

    if session.exec(
        select(Collection).where(Collection.parent_id == collection_id)
    ).first():
        raise HTTPException(status_code=409, detail="collection_has_children")
    if _collection_model_count(session, cat.path):
        raise HTTPException(status_code=409, detail="collection_has_models")

    cat.deleted_at = utcnow()
    session.add(cat)
    session.commit()
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# Tags
# ---------------------------------------------------------------------------


def _tag_model_count(session: Session, tag_id: int) -> int:
    count = session.exec(
        select(func.count(ModelTagLink.model_id))
        .join(Model, Model.id == ModelTagLink.model_id)
        .where(ModelTagLink.tag_id == tag_id, live(Model))
    ).one()
    return int(count or 0)


@router.get(
    "/tags",
    response_model=List[TagRead],
    summary="List all tags with model counts",
)
def list_tags(session: Session = Depends(get_session)) -> List[TagRead]:
    tags = session.exec(
        select(Tag).where(live(Tag)).order_by(Tag.name)  # type: ignore[union-attr]
    ).all()
    return [
        TagRead(
            id=t.id,
            name=t.name,
            slug=t.slug,
            model_count=_tag_model_count(session, t.id),
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
