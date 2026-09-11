"""SQL authorization over all reserved members, before any projection or write."""

from sqlalchemy import and_, or_, true
from sqlmodel import Session, col, select

from app.core.errors import ErrorKind, OperationError
from app.db.models import CollectionRole, Model, ModelFamily, ModelFamilyMember, User
from app.db.scopes import live, trashed
from app.modules.identity import rbac


def reserved_membership():
    """A live reservation or a trashed Family's restorable membership."""
    return or_(
        col(ModelFamilyMember.detached_at).is_(None),
        and_(trashed(ModelFamily), ModelFamilyMember.detach_reason == "family_trashed"),
    )


def _members():
    return (
        select(ModelFamilyMember.id)
        .where(
            ModelFamilyMember.family_id == ModelFamily.id,
            col(ModelFamilyMember.model_id).is_not(None),
            reserved_membership(),
        )
        .correlate(ModelFamily)
    )


def editable_clause(session: Session, user: User):
    if user.is_superuser:
        return true()
    allowed = rbac.accessible_collection_ids(session, user, CollectionRole.EDIT)
    members = _members()
    forbidden_models = select(Model.id).where(
        or_(
            col(Model.collection_id).is_(None), col(Model.collection_id).not_in(allowed)
        )
    )
    forbidden_member = members.where(
        col(ModelFamilyMember.model_id).in_(forbidden_models)
    ).exists()
    collection_allowed = or_(
        col(ModelFamily.collection_id).is_(None),
        col(ModelFamily.collection_id).in_(allowed),
    )
    empty_authority = or_(
        ModelFamily.created_by == user.id,
        col(ModelFamily.collection_id).in_(allowed),
    )
    return and_(
        collection_allowed,
        ~forbidden_member,
        or_(members.exists(), empty_authority),
    )


def visible_clause(session: Session, user: User):
    if user.is_superuser:
        return true()
    allowed = rbac.accessible_collection_ids(session, user, CollectionRole.VIEW)
    visible_models = select(Model.id).where(
        live(Model), col(Model.collection_id).in_(allowed)
    )
    visible_member = (
        _members().where(col(ModelFamilyMember.model_id).in_(visible_models)).exists()
    )
    return and_(
        or_(
            col(ModelFamily.collection_id).is_(None),
            col(ModelFamily.collection_id).in_(allowed),
        ),
        or_(visible_member, editable_clause(session, user)),
    )


def require(
    session: Session,
    user: User,
    family_id: int,
    *,
    edit: bool = False,
    include_trashed: bool = False,
) -> ModelFamily:
    stmt = select(ModelFamily).where(
        ModelFamily.id == family_id, visible_clause(session, user)
    )
    if not include_trashed:
        stmt = stmt.where(live(ModelFamily))
    family = session.exec(stmt).first()
    if family is None:
        raise OperationError("family_not_found", kind=ErrorKind.NOT_FOUND)
    if (
        edit
        and session.exec(
            select(ModelFamily.id).where(
                ModelFamily.id == family_id, editable_clause(session, user)
            )
        ).first()
        is None
    ):
        raise OperationError("family_permission_denied", kind=ErrorKind.FORBIDDEN)
    return family


def lock_families(session: Session, family_ids: list[int]) -> None:
    """Ascending PostgreSQL row locks; SQLite writes also use version CAS."""
    session.exec(
        select(ModelFamily)
        .where(col(ModelFamily.id).in_(sorted(set(family_ids))))
        .order_by(ModelFamily.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).all()


def require_models(
    session: Session,
    user: User,
    model_ids: list[int],
    *,
    edit: bool = True,
) -> list[Model]:
    rows = list(session.exec(select(Model).where(col(Model.id).in_(model_ids))).all())
    roles = rbac.effective_roles_for_collections(
        session, user, (row.collection_id for row in rows)
    )
    if len(rows) != len(set(model_ids)) or any(
        row.deleted_at is not None
        or not rbac.role_allows(roles.get(row.collection_id), CollectionRole.VIEW)
        for row in rows
    ):
        raise OperationError("family_member_not_found", kind=ErrorKind.NOT_FOUND)
    if edit and any(
        not rbac.role_allows(roles.get(row.collection_id), CollectionRole.EDIT)
        for row in rows
    ):
        raise OperationError("family_permission_denied", kind=ErrorKind.FORBIDDEN)
    return rows
