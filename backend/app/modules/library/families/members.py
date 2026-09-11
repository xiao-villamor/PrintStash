"""Explicit membership changes preserve every Model and its own print context."""

from sqlmodel import Session, col, select

from app.core.errors import ErrorKind, OperationError
from app.core.time import utcnow
from app.db.models import ModelFamily, ModelFamilyMember, User
from app.schemas.families import (
    FamilyMemberAdd,
    FamilyMemberInput,
    FamilyMemberMove,
    FamilyMemberUpdate,
)

from .access import lock_families, require, require_models
from .mutations import active_member, insert_member, record, touch


def require_member(
    session: Session, family: ModelFamily, member_id: int
) -> ModelFamilyMember:
    member = session.exec(
        select(ModelFamilyMember).where(
            ModelFamilyMember.id == member_id,
            ModelFamilyMember.family_id == family.id,
            col(ModelFamilyMember.detached_at).is_(None),
        )
    ).first()
    if member is None:
        raise OperationError("family_member_not_found", kind=ErrorKind.NOT_FOUND)
    return member


def add(
    session: Session, user: User, family_id: int, data: FamilyMemberAdd
) -> ModelFamilyMember:
    lock_families(session, [family_id])
    family = require(session, user, family_id, edit=True)
    require_models(session, user, [data.model_id])
    existing = active_member(session, data.model_id)
    payload = FamilyMemberInput.model_validate(data.model_dump(exclude={"version"}))
    if existing is not None:
        if existing.family_id != family_id:
            raise OperationError("family_membership_conflict", kind=ErrorKind.CONFLICT)
        if all(
            getattr(existing, key) == value
            for key, value in payload.model_dump().items()
        ):
            return existing
        raise OperationError("family_member_update_required", kind=ErrorKind.CONFLICT)
    touch(session, user, family, data.version)
    member = insert_member(session, user, family, payload)
    record(
        session,
        user,
        family,
        "add",
        {"member_id": member.id, "model_id": member.model_id},
    )
    return member


def update_member(
    session: Session,
    user: User,
    family_id: int,
    member_id: int,
    data: FamilyMemberUpdate,
) -> ModelFamilyMember:
    lock_families(session, [family_id])
    family = require(session, user, family_id, edit=True)
    member = require_member(session, family, member_id)
    changes = data.model_dump(exclude_unset=True, exclude={"version"})
    if member.id == family.canonical_member_id and (
        "role" in changes
        or changes.get("scale_factor", 1.0) != 1.0
        or changes.get("mirrored", False)
    ):
        raise OperationError("family_canonical_invalid", kind=ErrorKind.UNPROCESSABLE)
    touch(session, user, family, data.version)
    before = {key: getattr(member, key) for key in changes}
    for key, value in changes.items():
        setattr(member, key, value)
    if "scale_factor" in changes:
        member.relative_to_member_id = family.canonical_member_id
    if "mirrored" in changes or "mirror_verified" in changes:
        member.mirror_reference_member_id = family.canonical_member_id
        member.mirror_verified = bool(changes.get("mirror_verified", False))
        member.relative_review_required = family.canonical_member_id is None
    member.updated_at, member.updated_by = utcnow(), user.id
    session.add(member)
    record(
        session,
        user,
        family,
        "member_update",
        {"member_id": member.id, "before": before, "after": changes},
    )
    return member


def archive_member(
    session: Session,
    user: User,
    family: ModelFamily,
    member: ModelFamilyMember,
    reason: str,
) -> None:
    member.detached_at = utcnow()
    member.detached_by = user.id
    member.detach_reason = reason
    member.updated_at, member.updated_by = utcnow(), user.id
    if family.canonical_member_id == member.id:
        family.canonical_member_id = None
        session.add(family)
    if family.cover_model_id == member.model_id:
        family.cover_model_id = None
        session.add(family)
    session.add(member)
    session.flush()


def detach(
    session: Session, user: User, family_id: int, member_id: int, version: int
) -> None:
    lock_families(session, [family_id])
    family = require(session, user, family_id, edit=True)
    member = require_member(session, family, member_id)
    touch(session, user, family, version)
    archive_member(session, user, family, member, "removed")
    record(
        session,
        user,
        family,
        "detach",
        {"member_id": member.id, "model_id": member.model_id},
    )


def move(
    session: Session, user: User, destination_id: int, data: FamilyMemberMove
) -> ModelFamilyMember:
    if data.source_family_id == destination_id:
        raise OperationError(
            "family_move_destination_invalid", kind=ErrorKind.UNPROCESSABLE
        )
    lock_families(session, [data.source_family_id, destination_id])
    source = require(session, user, data.source_family_id, edit=True)
    destination = require(session, user, destination_id, edit=True)
    require_models(session, user, [data.model_id])
    member = active_member(session, data.model_id)
    if member is None or member.family_id != source.id:
        raise OperationError("family_membership_conflict", kind=ErrorKind.CONFLICT)
    touch(session, user, source, data.source_version)
    touch(session, user, destination, data.destination_version)
    archive_member(session, user, source, member, "moved")
    values = data.model_dump(
        exclude={"source_family_id", "source_version", "destination_version"}
    )
    if "transformation_note" not in data.model_fields_set:
        values["transformation_note"] = member.transformation_note
    added = insert_member(
        session, user, destination, FamilyMemberInput.model_validate(values)
    )
    if "mirrored" not in data.model_fields_set:
        added.mirrored = member.mirrored
        added.mirror_verified = False
        added.mirror_reference_member_id = member.mirror_reference_member_id
        added.relative_review_required = True
        session.add(added)
    changes = {
        "model_id": data.model_id,
        "source_family_id": source.id,
        "destination_family_id": destination.id,
    }
    record(session, user, source, "move_out", changes)
    record(session, user, destination, "move_in", changes)
    return added
