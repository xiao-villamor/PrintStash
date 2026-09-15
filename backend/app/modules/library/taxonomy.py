"""Collection (hierarchical) + Tag (flat) services."""

from __future__ import annotations

from typing import Iterable, List, Optional

from sqlmodel import Session, select

from app.db.models import Collection, Tag
from app.db.projections import content_changed
from app.db.scopes import live, trashed
from app.modules.storage.storage import slugify


def resolve_or_create_collection(
    session: Session, raw_path: Optional[str]
) -> Optional[Collection]:
    """Resolve a path like 'Functional/Brackets' to a Collection row, creating
    any missing nodes along the way. Returns None for empty input.

    Path segments are split on '/', whitespace-trimmed, and slugified for
    `path` matching while preserving the user's casing in `name`.
    """
    if not raw_path:
        return None
    segments = [s.strip() for s in raw_path.split("/") if s.strip()]
    if not segments:
        return None

    parent: Optional[Collection] = None
    materialised_slug: List[str] = []
    for name in segments:
        slug = slugify(name)
        materialised_slug.append(slug)
        path = "/".join(materialised_slug)
        existing = session.exec(
            select(Collection).where(Collection.path == path)
        ).first()
        if existing is None:
            existing = Collection(
                name=name,
                slug=slug,
                parent_id=parent.id if parent else None,
                path=path,
            )
            session.add(existing)
            content_changed(session, "collection", (row.id for row in (existing,)))
            session.commit()
            session.refresh(existing)
        elif existing.deleted_at is not None:
            # Revive a trashed node rather than attaching models to a dead row.
            existing.deleted_at = None
            existing.deleted_by = None
            session.add(existing)
            content_changed(session, "collection", (row.id for row in (existing,)))
            session.commit()
            session.refresh(existing)
        parent = existing
    return parent


def resolve_or_create_collection_in_transaction(
    session: Session, raw_path: str
) -> Optional[Collection]:
    """Resolve/create a collection path without committing caller transaction."""
    segments = [s.strip() for s in raw_path.split("/") if s.strip()]
    if not segments:
        return None
    parent: Optional[Collection] = None
    materialised_slug: List[str] = []
    for name in segments:
        slug = slugify(name)
        materialised_slug.append(slug)
        path = "/".join(materialised_slug)
        existing = session.exec(
            select(Collection).where(Collection.path == path, live(Collection))
        ).first()
        if existing is None:
            existing = session.exec(
                select(Collection).where(Collection.path == path, trashed(Collection))
            ).first()
        if existing is None:
            existing = Collection(
                name=name,
                slug=slug,
                parent_id=parent.id if parent else None,
                path=path,
            )
            session.add(existing)
            content_changed(session, "collection", (row.id for row in (existing,)))
            session.flush()
        elif existing.deleted_at is not None:
            existing.deleted_at = None
            existing.deleted_by = None
            session.add(existing)
            content_changed(session, "collection", (row.id for row in (existing,)))
            session.flush()
        parent = existing
    return parent


def list_collections(session: Session) -> List[Collection]:
    return list(session.exec(select(Collection).order_by(Collection.path)).all())


def collection_descendant_paths(session: Session, root_path: str) -> List[str]:
    """Return root_path plus all descendant paths (for prefix filtering)."""
    stmt = select(Collection.path).where(
        (Collection.path == root_path) | (Collection.path.startswith(root_path + "/"))  # type: ignore[attr-defined]
    )
    return list(session.exec(stmt).all())


def resolve_or_create_tags(session: Session, names: Iterable[str]) -> List[Tag]:
    """Map a list of tag names to Tag rows, creating any that don't exist."""
    out: List[Tag] = []
    seen: set[str] = set()
    for raw in names:
        name = raw.strip()
        if not name:
            continue
        slug = slugify(name)
        if slug in seen:
            continue
        seen.add(slug)
        existing = session.exec(select(Tag).where(Tag.slug == slug)).first()
        if existing is None:
            existing = Tag(name=name, slug=slug)
            session.add(existing)
            session.commit()
            session.refresh(existing)
        elif existing.deleted_at is not None:
            # Revive a trashed tag rather than linking a model to a dead row
            # (mirrors resolve_or_create_collection). Otherwise the link points
            # at a tag that list_tags hides via live(Tag) — a ghost tag.
            existing.deleted_at = None
            existing.deleted_by = None
            session.add(existing)
            session.commit()
            session.refresh(existing)
        out.append(existing)
    return out


def resolve_or_create_tags_in_transaction(
    session: Session, names: Iterable[str]
) -> List[Tag]:
    """Resolve live tags without committing the caller's transaction."""
    out: List[Tag] = []
    seen: set[str] = set()
    for raw in names:
        name = raw.strip()
        slug = slugify(name)
        if not name or not slug or slug in seen:
            continue
        seen.add(slug)
        existing = session.exec(select(Tag).where(Tag.slug == slug, live(Tag))).first()
        if existing is None:
            existing = session.exec(
                select(Tag).where(Tag.slug == slug, trashed(Tag))
            ).first()
        if existing is None:
            existing = Tag(name=name, slug=slug)
            session.add(existing)
            session.flush()
        elif existing.deleted_at is not None:
            existing.deleted_at = None
            existing.deleted_by = None
            session.add(existing)
            session.flush()
        out.append(existing)
    return out


def parse_tag_input(value: Optional[str]) -> List[str]:
    if not value:
        return []
    return [t.strip() for t in value.split(",") if t.strip()]


def list_tags(session: Session) -> List[Tag]:
    return list(session.exec(select(Tag).order_by(Tag.name)).all())
