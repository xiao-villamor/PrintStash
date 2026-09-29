"""Collection access predicates for batched library reads."""

from __future__ import annotations

from sqlmodel import Session, select

from app.db.models import (
    SENTINEL_MODEL_HASH,
    CollectionRole,
    Model,
    User,
)
from app.db.scopes import live
from app.modules.identity import rbac


def _apply_model_access(stmt, session: Session, user: User):
    if user.is_superuser:
        return stmt
    return stmt.where(
        Model.collection_id.in_(  # type: ignore[union-attr]
            rbac.accessible_collection_ids_stmt(session, user, CollectionRole.VIEW)
        )
    )


def accessible_live_model_ids_stmt(session: Session, user: User):
    """Reusable SQL scope for accessible live library Models.

    Large exports must keep this as a subquery instead of first materializing
    every Model response (or a Python ID list). That keeps RBAC identical to the
    normal read models without paying for their Artifact/Metadata composition.
    """
    stmt = select(Model.id).where(
        live(Model),
        Model.hash != SENTINEL_MODEL_HASH,
    )
    return _apply_model_access(stmt, session, user)


def _effective_model_role(
    session: Session,
    user: User,
    model: Model,
) -> CollectionRole | None:
    return rbac.effective_collection_role(session, user, model.collection_id)
