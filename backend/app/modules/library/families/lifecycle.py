"""Release and restore reservations without changing member content."""

from sqlmodel import Session, col, delete, select

from app.core.errors import ErrorKind, OperationError
from app.core.time import utcnow
from app.db.models import (
    ModelFamily,
    ModelFamilyMember,
    ModelFamilyStar,
    ModelFamilyTagLink,
    User,
)
from app.modules.administration.audit import current_audit_context
from app.runtime.maintenance import guarded_destructive_operation

from .access import lock_families, require
from .covers import clear_upload
from .mutations import record, touch


@guarded_destructive_operation
def purge_family(session: Session, user: User, family_id: int, version: int) -> None:
    lock_families(session, [family_id])
    family = require(session, user, family_id, edit=True, include_trashed=True)
    if family.deleted_at is None:
        raise OperationError("family_trash_required", kind=ErrorKind.CONFLICT)
    touch(session, user, family, version)
    clear_upload(session, family)
    record(session, user, family, "purge", {"name": family.name})
    family.canonical_member_id = None
    session.add(family)
    session.flush()
    # Explicit order also works on adapters without ORM relationships loaded.
    for table in (ModelFamilyStar, ModelFamilyTagLink, ModelFamilyMember):
        session.exec(delete(table).where(table.family_id == family_id))
    session.delete(family)


def trash_family(session: Session, user: User, family_id: int, version: int) -> None:
    lock_families(session, [family_id])
    family = require(session, user, family_id, edit=True, include_trashed=True)
    if family.deleted_at is not None:
        return
    touch(session, user, family, version)
    instant = utcnow()
    members = session.exec(
        select(ModelFamilyMember).where(
            ModelFamilyMember.family_id == family_id,
            col(ModelFamilyMember.detached_at).is_(None),
        )
    ).all()
    for member in members:
        member.detached_at, member.detached_by = instant, user.id
        member.detach_reason = "family_trashed"
        member.updated_at, member.updated_by = instant, user.id
        session.add(member)
    family.deleted_at, family.deleted_by = instant, user.id
    session.add(family)
    record(
        session,
        user,
        family,
        "trash",
        {"member_ids": [member.id for member in members]},
    )


def restore_family(
    session: Session,
    user: User,
    family_id: int,
    version: int,
) -> tuple[ModelFamily, list[int]]:
    lock_families(session, [family_id])
    family = require(session, user, family_id, edit=True, include_trashed=True)
    if family.deleted_at is None:
        return family, []
    members = session.exec(
        select(ModelFamilyMember)
        .where(
            ModelFamilyMember.family_id == family_id,
            ModelFamilyMember.detach_reason == "family_trashed",
        )
        .order_by(ModelFamilyMember.id)
    ).all()
    model_ids = [member.model_id for member in members if member.model_id is not None]
    conflict = session.exec(
        select(ModelFamilyMember.id).where(
            col(ModelFamilyMember.model_id).in_(model_ids),
            col(ModelFamilyMember.detached_at).is_(None),
        )
    ).first()
    if conflict is not None:
        raise OperationError("family_restore_conflict", kind=ErrorKind.CONFLICT)
    omitted = list(
        session.exec(
            select(ModelFamilyMember.id)
            .where(
                ModelFamilyMember.family_id == family_id,
                ModelFamilyMember.detach_reason == "model_purged",
                col(ModelFamilyMember.model_id).is_(None),
            )
            .order_by(ModelFamilyMember.id)
        ).all()
    )
    touch(session, user, family, version)
    for member in members:
        member.detached_at = member.detached_by = member.detach_reason = None
        member.updated_at, member.updated_by = utcnow(), user.id
        session.add(member)
    family.deleted_at = family.deleted_by = None
    session.add(family)
    record(
        session,
        user,
        family,
        "restore",
        {
            "member_ids": [member.id for member in members],
            "omitted_member_ids": omitted,
        },
    )
    return family, [int(member_id) for member_id in omitted]


def purge_model_references(session: Session, model_id: int) -> None:
    """Called only by Model purge, after its own authorization and storage checks.

    Historical memberships keep their identity but lose the Model reference.
    Cover and canonical references are cleared explicitly before the Model FK
    is removed. This operation does not impose sibling EDIT on Model lifecycle.
    """
    members = session.exec(
        select(ModelFamilyMember).where(ModelFamilyMember.model_id == model_id)
    ).all()
    family_ids = {member.family_id for member in members}
    family_ids.update(
        session.exec(
            select(ModelFamily.id).where(ModelFamily.cover_model_id == model_id)
        ).all()
    )
    if not family_ids:
        return
    lock_families(session, list(family_ids))
    families = {
        family.id: family
        for family in session.exec(
            select(ModelFamily).where(col(ModelFamily.id).in_(family_ids))
        ).all()
    }
    actor_id, _ = current_audit_context()
    actor = session.get(User, actor_id) if actor_id is not None else None
    instant = utcnow()
    for member in members:
        family = families[member.family_id]
        if family.canonical_member_id == member.id:
            family.canonical_member_id = None
        member.model_id = None
        member.detached_at = member.detached_at or instant
        member.detached_by = actor_id
        member.detach_reason = "model_purged"
        member.updated_at, member.updated_by = instant, actor_id
        session.add(member)
    for family in families.values():
        if family.cover_model_id == model_id:
            family.cover_model_id = None
        family.version += 1
        family.updated_at, family.updated_by = instant, actor_id
        session.add(family)
        record(session, actor, family, "model_purge", {"model_id": model_id})
    session.flush()


def purge_collection_references(session: Session, collection_id: int) -> None:
    """A Collection purge leaves the independent Family and its members intact."""
    family_ids = list(
        session.exec(
            select(ModelFamily.id).where(ModelFamily.collection_id == collection_id)
        ).all()
    )
    if not family_ids:
        return
    lock_families(session, family_ids)
    actor_id, _ = current_audit_context()
    actor = session.get(User, actor_id) if actor_id is not None else None
    for family in session.exec(
        select(ModelFamily).where(ModelFamily.collection_id == collection_id)
    ).all():
        family.collection_id = None
        family.version += 1
        family.updated_at, family.updated_by = utcnow(), actor_id
        session.add(family)
        record(
            session, actor, family, "collection_purge", {"collection_id": collection_id}
        )
    session.flush()
