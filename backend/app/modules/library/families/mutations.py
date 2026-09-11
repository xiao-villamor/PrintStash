"""Transactional Family edits. The caller commits relationships and audit together."""

import json

from printstash_core.files import slugify
from sqlalchemy import update
from sqlmodel import Session, col, select

from app.core.errors import ErrorKind, OperationError
from app.core.time import utcnow
from app.db.models import AuditLog, CollectionRole, ModelFamily, ModelFamilyMember, User
from app.modules.identity import rbac
from app.schemas.families import FamilyCreate, FamilyMemberInput

from .access import require_models


def record(
    session: Session,
    user: User | None,
    family: ModelFamily,
    action: str,
    changes: dict,
) -> None:
    session.add(
        AuditLog(
            actor_id=user.id if user is not None else None,
            action=f"family.{action}",
            resource_type="model_families",
            resource_id=family.id,
            diff_json=json.dumps(changes, default=str),
        )
    )


def touch(session: Session, user: User, family: ModelFamily, version: int) -> None:
    changed = session.execute(
        update(ModelFamily)
        .where(ModelFamily.id == family.id, ModelFamily.version == version)
        .values(version=version + 1, updated_at=utcnow(), updated_by=user.id)
        .execution_options(synchronize_session="fetch")
    )
    if changed.rowcount != 1:
        raise OperationError("family_revision_conflict", kind=ErrorKind.CONFLICT)


def available_slug(session: Session, name: str) -> str:
    base = slugify(name)[:235] or "family"
    candidate, suffix = base, 2
    while (
        session.exec(
            select(ModelFamily.id).where(ModelFamily.slug == candidate)
        ).first()
        is not None
    ):
        candidate = f"{base}-{suffix}"
        suffix += 1
    return candidate


def active_member(session: Session, model_id: int) -> ModelFamilyMember | None:
    return session.exec(
        select(ModelFamilyMember).where(
            ModelFamilyMember.model_id == model_id,
            col(ModelFamilyMember.detached_at).is_(None),
        )
    ).first()


def insert_member(
    session: Session,
    user: User,
    family: ModelFamily,
    data: FamilyMemberInput,
    *,
    joined_via: str = "manual",
) -> ModelFamilyMember:
    member = ModelFamilyMember(
        family_id=int(family.id),
        **data.model_dump(),
        joined_via=joined_via,
        created_by=user.id,
        updated_by=user.id,
        relative_to_member_id=family.canonical_member_id,
        mirror_reference_member_id=family.canonical_member_id,
        relative_review_required=(data.scale_factor is not None or data.mirror_verified)
        and family.canonical_member_id is None,
    )
    session.add(member)
    session.flush()
    return member


def create(session: Session, user: User, data: FamilyCreate) -> ModelFamily:
    ids = [member.model_id for member in data.members]
    require_models(session, user, ids)
    if data.collection_id is not None:
        rbac.require_collection_role(
            session, user, data.collection_id, CollectionRole.EDIT
        )
    if (
        session.exec(
            select(ModelFamilyMember.id).where(
                col(ModelFamilyMember.model_id).in_(ids),
                col(ModelFamilyMember.detached_at).is_(None),
            )
        ).first()
        is not None
    ):
        raise OperationError("family_membership_conflict", kind=ErrorKind.CONFLICT)
    family = ModelFamily(
        name=data.name,
        slug=available_slug(session, data.name),
        description=data.description,
        collection_id=data.collection_id,
        created_by=user.id,
        updated_by=user.id,
    )
    session.add(family)
    session.flush()
    members = [insert_member(session, user, family, member) for member in data.members]
    canonical = next(
        member for member in members if member.model_id == data.canonical_model_id
    )
    family.canonical_member_id = canonical.id
    canonical.role = "canonical"
    canonical.scale_factor = 1.0
    canonical.mirrored = False
    canonical.mirror_verified = True
    for member in members:
        member.relative_to_member_id = canonical.id
        member.mirror_reference_member_id = canonical.id
        member.relative_review_required = False
        session.add(member)
    session.add(family)
    record(
        session,
        user,
        family,
        "create",
        {"model_ids": ids, "canonical_model_id": data.canonical_model_id},
    )
    session.flush()
    return family
