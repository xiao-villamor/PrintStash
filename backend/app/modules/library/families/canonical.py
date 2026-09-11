"""Human selection and trustworthy rebasing, without automatic promotion."""

from sqlmodel import Session, col, select

from app.core.errors import ErrorKind, OperationError
from app.core.time import utcnow
from app.db.models import ModelFamily, ModelFamilyMember, User
from app.schemas.families import FamilyCanonicalChange

from .access import lock_families, require, require_models
from .mutations import record, touch
from .relative import rebase_scale


def choose(
    session: Session, user: User, family_id: int, data: FamilyCanonicalChange
) -> ModelFamily:
    lock_families(session, [family_id])
    family = require(session, user, family_id, edit=True)
    members = list(
        session.exec(
            select(ModelFamilyMember).where(
                ModelFamilyMember.family_id == family_id,
                col(ModelFamilyMember.detached_at).is_(None),
            )
        ).all()
    )
    chosen = next((member for member in members if member.id == data.member_id), None)
    if chosen is None or chosen.model_id is None:
        raise OperationError("family_canonical_invalid", kind=ErrorKind.UNPROCESSABLE)
    require_models(session, user, [chosen.model_id])
    if family.version != data.version:
        raise OperationError("family_revision_conflict", kind=ErrorKind.CONFLICT)
    if family.canonical_member_id == chosen.id:
        return family
    touch(session, user, family, data.version)
    previous_id = family.canonical_member_id
    reference_scale = chosen.scale_factor
    scale_base = chosen.relative_to_member_id
    mirror_base = chosen.mirror_reference_member_id
    reference_mirrored = chosen.mirrored
    mirror_verified = chosen.mirror_verified
    for member in members:
        if member.id == previous_id and member.id != chosen.id:
            member.role = data.previous_role
            session.add(member)
    # The partial unique index must see demotion before promotion.
    session.flush()
    for member in members:
        member.scale_factor = rebase_scale(
            member.scale_factor,
            reference_scale,
            same_reference=scale_base is not None
            and member.relative_to_member_id == scale_base,
        )
        member.relative_to_member_id = (
            chosen.id if member.scale_factor is not None else None
        )
        if (
            mirror_verified
            and member.mirror_verified
            and mirror_base is not None
            and member.mirror_reference_member_id == mirror_base
        ):
            member.mirrored = member.mirrored != reference_mirrored
            member.mirror_reference_member_id = chosen.id
            member.relative_review_required = False
        elif member.id != chosen.id:
            # Preserve the prior measurement's reference instead of claiming it
            # was measured against the newly chosen Model.
            member.relative_review_required = True
            member.mirror_verified = False
        member.updated_at, member.updated_by = utcnow(), user.id
        session.add(member)
    chosen.role, chosen.scale_factor, chosen.mirrored = "canonical", 1.0, False
    chosen.relative_to_member_id = chosen.id
    chosen.mirror_reference_member_id = chosen.id
    chosen.mirror_verified, chosen.relative_review_required = True, False
    family.canonical_member_id = chosen.id
    session.add(chosen)
    session.add(family)
    record(
        session,
        user,
        family,
        "canonical",
        {
            "previous_member_id": previous_id,
            "member_id": chosen.id,
            "previous_role": data.previous_role,
        },
    )
    return family
