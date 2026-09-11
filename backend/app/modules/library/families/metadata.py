"""Shared metadata requires EDIT over every reserved member."""

from sqlmodel import Session, delete, select

from app.core.errors import ErrorKind, OperationError
from app.db.models import (
    CollectionRole,
    ModelFamily,
    ModelFamilyStar,
    ModelFamilyTagLink,
    User,
)
from app.modules.identity import rbac
from app.modules.library import taxonomy
from app.schemas.families import FamilyUpdate

from .access import lock_families, require, require_models
from .covers import clear_upload
from .mutations import active_member, record, touch


def update_metadata(
    session: Session, user: User, family_id: int, data: FamilyUpdate
) -> ModelFamily:
    lock_families(session, [family_id])
    family = require(session, user, family_id, edit=True)
    changes = data.model_dump(mode="json", exclude_unset=True, exclude={"version"})
    if data.collection_id is not None:
        rbac.require_collection_role(
            session, user, data.collection_id, CollectionRole.EDIT
        )
    if data.cover_model_id is not None:
        require_models(session, user, [data.cover_model_id], edit=False)
        member = active_member(session, data.cover_model_id)
        if member is None or member.family_id != family_id:
            raise OperationError("family_cover_invalid", kind=ErrorKind.UNPROCESSABLE)
    touch(session, user, family, data.version)
    tag_names = changes.pop("tags", None)
    before = {key: getattr(family, key) for key in changes}
    if "cover_image_url" in changes or "cover_model_id" in changes:
        clear_upload(session, family)
        if data.cover_model_id is not None:
            family.cover_image_url = None
        if data.cover_image_url is not None:
            family.cover_model_id = None
    if tag_names is not None:
        tags = taxonomy.resolve_or_create_tags_in_transaction(session, tag_names)
        session.exec(
            delete(ModelFamilyTagLink).where(ModelFamilyTagLink.family_id == family_id)
        )
        session.add_all(
            [
                ModelFamilyTagLink(family_id=family_id, tag_id=int(tag.id))
                for tag in tags
            ]
        )
    for key, value in changes.items():
        setattr(family, key, value)
    session.add(family)
    record(
        session,
        user,
        family,
        "update",
        {"before": before, "after": changes, "tags": tag_names},
    )
    return family


def star(session: Session, user: User, family_id: int, starred: bool) -> None:
    require(session, user, family_id)
    existing = session.exec(
        select(ModelFamilyStar).where(
            ModelFamilyStar.family_id == family_id,
            ModelFamilyStar.user_id == user.id,
        )
    ).first()
    if starred and existing is None:
        session.add(ModelFamilyStar(family_id=family_id, user_id=int(user.id)))
    elif not starred and existing is not None:
        session.delete(existing)
