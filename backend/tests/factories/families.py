"""Family states without changing their Models' content or identity."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlmodel import Session

from app.core.time import utcnow
from app.db.models import (
    Collection,
    Model,
    ModelFamily,
    ModelFamilyMember,
    ModelFamilyStar,
    ModelFamilyTagLink,
    Tag,
    User,
)
from tests.factories._support import nth, save


def build_family(
    session: Session,
    name: str = "Bracket variations",
    *,
    collection: Collection | None = None,
    trashed: bool | datetime = False,
    **overrides: Any,
) -> ModelFamily:
    """An empty grouping after detach, or the shell for a membership scenario."""
    index = nth("family")
    overrides.setdefault("slug", f"family-{index}")
    overrides.setdefault("export_id", f"00000000-0000-4000-8000-{index:012d}")
    if collection is not None:
        overrides.setdefault("collection_id", collection.id)
    if trashed:
        overrides.setdefault(
            "deleted_at", trashed if isinstance(trashed, datetime) else utcnow()
        )
    return save(session, ModelFamily(name=name, **overrides))


def build_family_member(
    session: Session,
    family: ModelFamily,
    model: Model | None,
    *,
    canonical: bool = False,
    detached: str | None = None,
    **overrides: Any,
) -> ModelFamilyMember:
    """Reserved membership; canonical=True also sets the human selection."""
    overrides.setdefault("sort_order", nth("family_member") - 1)
    if detached or family.deleted_at is not None:
        overrides.setdefault("detached_at", family.deleted_at or utcnow())
        overrides.setdefault("detach_reason", detached or "family_trashed")
    if canonical:
        if family.canonical_member_id is not None:
            raise ValueError("family already has a canonical selection")
        overrides.setdefault("role", "canonical")
        overrides.setdefault("scale_factor", 1.0)
        overrides.setdefault("mirror_verified", True)
    member = save(
        session,
        ModelFamilyMember(
            family_id=int(family.id),
            model_id=model.id if model is not None else None,
            **overrides,
        ),
    )
    if canonical:
        family.canonical_member_id = member.id
        member.relative_to_member_id = member.id
        member.mirror_reference_member_id = member.id
        session.add(member)
        save(session, family)
    return member


def build_family_star(
    session: Session, user: User, family: ModelFamily, **overrides: Any
) -> ModelFamilyStar:
    return save(
        session,
        ModelFamilyStar(user_id=int(user.id), family_id=int(family.id), **overrides),
    )


def tag_family(session: Session, family: ModelFamily, tag: Tag) -> ModelFamilyTagLink:
    return save(
        session, ModelFamilyTagLink(family_id=int(family.id), tag_id=int(tag.id))
    )
