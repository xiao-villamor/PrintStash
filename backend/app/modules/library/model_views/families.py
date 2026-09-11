"""Authorized, batched Family cards and Model membership summaries."""

from collections import defaultdict

from sqlalchemy import func, or_
from sqlmodel import Session, col, select

from app.db.models import (
    Collection,
    CollectionRole,
    Model,
    ModelFamily,
    ModelFamilyMember,
    ModelFamilyStar,
    ModelFamilyTagLink,
    Tag,
    User,
)
from app.db.scopes import live
from app.modules.identity import rbac
from app.modules.library.families.access import (
    editable_clause,
    reserved_membership,
    visible_clause,
)
from app.schemas.families import FamilyRead


def family_reads(
    session: Session,
    user: User,
    families: list[ModelFamily],
) -> dict[int, FamilyRead]:
    from .projections import thumb_url

    if not families:
        return {}
    ids = [int(family.id) for family in families]
    visible_ids = set(
        session.exec(
            select(ModelFamily.id).where(
                col(ModelFamily.id).in_(ids),
                visible_clause(session, user),
            )
        ).all()
    )
    editable_ids = set(
        session.exec(
            select(ModelFamily.id).where(
                col(ModelFamily.id).in_(visible_ids),
                editable_clause(session, user),
            )
        ).all()
    )
    stmt = (
        select(ModelFamilyMember, Model)
        .join(Model, Model.id == ModelFamilyMember.model_id)
        .join(ModelFamily, ModelFamily.id == ModelFamilyMember.family_id)
        .where(
            col(ModelFamilyMember.family_id).in_(visible_ids),
            reserved_membership(),
            live(Model),
        )
    )
    if not user.is_superuser:
        stmt = stmt.where(
            col(Model.collection_id).in_(
                rbac.accessible_collection_ids(session, user, CollectionRole.VIEW)
            )
        )
    counts = dict(
        session.execute(
            stmt.with_only_columns(
                ModelFamilyMember.family_id, func.count(ModelFamilyMember.id)
            ).group_by(ModelFamilyMember.family_id)
        ).all()
    )
    # Hydrate at most the two explicitly selected Models per Family. Counting
    # a large Family must not load every sibling into the ORM identity map.
    representatives = stmt.where(
        or_(
            ModelFamilyMember.id == ModelFamily.canonical_member_id,
            Model.id == ModelFamily.cover_model_id,
        )
    )
    members = defaultdict(list)
    for member, model in session.exec(representatives).all():
        members[member.family_id].append((member, model))
    tags = defaultdict(list)
    for family_id, name in session.exec(
        select(ModelFamilyTagLink.family_id, Tag.name)
        .join(Tag, Tag.id == ModelFamilyTagLink.tag_id)
        .where(col(ModelFamilyTagLink.family_id).in_(visible_ids), live(Tag))
        .order_by(Tag.name)
    ).all():
        tags[family_id].append(name)
    starred = set(
        session.exec(
            select(ModelFamilyStar.family_id).where(
                col(ModelFamilyStar.family_id).in_(visible_ids),
                ModelFamilyStar.user_id == user.id,
            )
        ).all()
    )
    collection_ids = {
        family.collection_id for family in families if family.id in visible_ids
    }
    collections = dict(
        session.exec(
            select(Collection.id, Collection.path).where(
                col(Collection.id).in_(collection_ids),
                live(Collection),
            )
        ).all()
    )
    result = {}
    for family in families:
        if family.id not in visible_ids:
            continue
        canonical = next(
            (
                (member, model)
                for member, model in members[family.id]
                if member.id == family.canonical_member_id
            ),
            None,
        )
        cover = next(
            (
                model
                for _, model in members[family.id]
                if model.id == family.cover_model_id
            ),
            None,
        )
        representative = cover or (canonical[1] if canonical else None)
        thumbnail = thumb_url(representative) if representative else None
        if family.cover_filename:
            thumbnail = f"/api/v1/families/{family.id}/cover?v={family.version}"
        result[int(family.id)] = FamilyRead(
            id=int(family.id),
            name=family.name,
            slug=family.slug,
            description=family.description,
            collection_id=family.collection_id,
            collection=collections.get(family.collection_id),
            version=family.version,
            canonical_model_id=canonical[1].id if canonical else None,
            canonical_member_id=canonical[0].id if canonical else None,
            cover_model_id=cover.id if cover else None,
            cover_thumbnail_url=thumbnail,
            cover_image_uploaded=bool(family.cover_filename),
            member_count=counts.get(family.id, 0),
            matching_visible_members=counts.get(family.id, 0),
            tags=tags[family.id],
            starred=family.id in starred,
            effective_role=CollectionRole.EDIT
            if family.id in editable_ids
            else CollectionRole.VIEW,
            created_at=family.created_at,
            updated_at=family.updated_at,
            deleted_at=family.deleted_at,
        )
    return result
