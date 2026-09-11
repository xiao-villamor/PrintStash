"""Explicit live-member actions compose existing Model commands atomically."""

from sqlmodel import Session, col, select

from app.core.errors import ErrorKind, OperationError
from app.db.models import ModelFamilyMember, User
from app.modules.library import commands
from app.modules.library.model_views.access import accessible_live_model_ids_stmt
from app.schemas.families import FamilyBulkCollection, FamilyBulkTags
from app.schemas.models import ModelBatchMove, ModelBatchResult, ModelBatchTags

from .access import lock_families, require
from .mutations import record, touch


def _live_member_ids(session: Session, user: User, family_id: int) -> list[int]:
    return list(
        session.exec(
            select(ModelFamilyMember.model_id)
            .where(
                ModelFamilyMember.family_id == family_id,
                col(ModelFamilyMember.detached_at).is_(None),
                col(ModelFamilyMember.model_id).in_(
                    accessible_live_model_ids_stmt(session, user)
                ),
            )
            .order_by(ModelFamilyMember.id)
        ).all()
    )


def _result(ids: list[int]) -> ModelBatchResult:
    return ModelBatchResult(
        succeeded_ids=ids, succeeded_count=len(ids), failed=[], failed_count=0
    )


def apply_tags(
    session: Session, user: User, family_id: int, data: FamilyBulkTags
) -> ModelBatchResult:
    lock_families(session, [family_id])
    family = require(session, user, family_id, edit=True)
    touch(session, user, family, data.version)
    ids = _live_member_ids(session, user, family_id)
    for start in range(0, len(ids), 500):
        commands.batch_tag_models(
            ModelBatchTags(
                model_ids=ids[start : start + 500], add=data.add, remove=data.remove
            ),
            user,
            session,
            commit=False,
        )
    record(
        session,
        user,
        family,
        "members.tags",
        {"model_ids": ids, "add": data.add, "remove": data.remove},
    )
    return _result(ids)


def move_collection(
    session: Session, user: User, family_id: int, data: FamilyBulkCollection
) -> ModelBatchResult:
    lock_families(session, [family_id])
    family = require(session, user, family_id, edit=True)
    touch(session, user, family, data.version)
    ids = _live_member_ids(session, user, family_id)
    for start in range(0, len(ids), 500):
        commands.batch_move_models(
            ModelBatchMove(
                model_ids=ids[start : start + 500], collection=data.collection
            ),
            user,
            session,
            commit=False,
        )
    record(
        session,
        user,
        family,
        "members.collection",
        {"model_ids": ids, "collection": data.collection},
    )
    return _result(ids)


def star_visible(
    session: Session, user: User, family_id: int, version: int
) -> ModelBatchResult:
    lock_families(session, [family_id])
    family = require(session, user, family_id)
    if family.version != version:
        raise OperationError("family_revision_conflict", kind=ErrorKind.CONFLICT)
    ids = _live_member_ids(session, user, family_id)
    for model_id in ids:
        commands.star_model(model_id, user, session, commit=False)
    record(session, user, family, "members.star", {"model_ids": ids})
    return _result(ids)
