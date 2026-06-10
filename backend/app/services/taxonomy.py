"""Collection (hierarchical) + Tag (flat) services."""

from __future__ import annotations

from typing import Iterable, List, Optional

from sqlmodel import Session, select

from app.db.models import Collection, Tag
from app.services.storage import slugify


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
            session.commit()
            session.refresh(existing)
        elif existing.deleted_at is not None:
            # Revive a trashed node rather than attaching models to a dead row.
            existing.deleted_at = None
            existing.deleted_by = None
            session.add(existing)
            session.commit()
            session.refresh(existing)
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
        out.append(existing)
    return out


def parse_tag_input(value: Optional[str]) -> List[str]:
    if not value:
        return []
    return [t.strip() for t in value.split(",") if t.strip()]


def list_tags(session: Session) -> List[Tag]:
    return list(session.exec(select(Tag).order_by(Tag.name)).all())
