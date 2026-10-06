"""Authoritative mixed card pages with revision-checked continuation.

The cursor is a signed position within a revision, not a historical snapshot.
Every query and projection must observe the same authority. Under read-committed
engines the second revision read rejects a race; under repeatable-read engines
the whole request observes one consistent database snapshot.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import Float, case, cast, func, literal, or_, select, union_all
from sqlalchemy.orm import lazyload
from sqlalchemy.orm.attributes import set_committed_value
from sqlmodel import Session, col

from app.core.config import settings
from app.core.errors import ErrorKind, OperationError
from app.db.models import (
    Collection,
    File,
    LibraryRevision,
    Model,
    MultipartModel,
    MultipartModelChoice,
    MultipartModelStar,
    MultipartModelTagLink,
    Tag,
    User,
)
from app.db.scopes import live
from app.modules.identity import rbac
from app.modules.library import multipart_models
from app.schemas.library_browse import (
    BrowseKind,
    BrowseModel,
    BrowseMultipart,
    BrowsePage,
    BrowseQuery,
    BrowseRevisionRead,
    BrowseThumbnail,
    BrowseThumbnailsRead,
    LibraryView,
)
from app.schemas.models import ModelFilters, ModelSort

from .access import accessible_live_model_ids_stmt
from .filters import filtered_with_rank
from .listing import read_items_by_ids
from .pagination import _sort_value_and_statement
from .thumbnails import thumb_url


class Cursor(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    version: Literal[1] = 1
    key: str
    revision: str
    offset: int = Field(gt=0, le=2**63 - 1)
    total: int = Field(ge=0)


def revision(session: Session) -> BrowseRevisionRead:
    """Cheap catalog/access discriminator; this never grants resource access."""
    row = session.execute(
        select(
            col(LibraryRevision.epoch),
            col(LibraryRevision.revision),
            col(LibraryRevision.authorization_revision),
        ).where(col(LibraryRevision.id) == 1)
    ).one_or_none()
    if row is None:
        raise OperationError("browse_revision_unavailable", kind=ErrorKind.UNAVAILABLE)
    return BrowseRevisionRead(
        browse_revision=f"{row.epoch}:{row.revision}",
        authorization_revision=f"{row.epoch}:{row.authorization_revision}",
    )


def _revision(session: Session) -> str:
    return revision(session).browse_revision


def _key(user: User, query: BrowseQuery) -> str:
    inputs = query.model_dump(mode="json", exclude={"cursor"})
    for field, value in inputs.items():
        if isinstance(value, list):
            inputs[field] = sorted(set(value))
    inputs["collection"] = (
        inputs["collection"].strip().strip("/").lower()
        if inputs["collection"] is not None
        else None
    )
    inputs["q"] = inputs["q"].strip() if inputs["q"] is not None else None
    raw = json.dumps(
        [user.id, user.auth_version, user.is_active, user.is_superuser, inputs],
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(raw).hexdigest()


def _encode(cursor: Cursor) -> str:
    data = (
        base64.urlsafe_b64encode(cursor.model_dump_json().encode()).decode().rstrip("=")
    )
    signature = hmac.new(
        settings.jwt_secret.encode(), data.encode(), hashlib.sha256
    ).hexdigest()
    return f"{data}.{signature}"


def _decode(token: str, key: str) -> Cursor:
    try:
        data, signature = token.split(".")
        expected = hmac.new(
            settings.jwt_secret.encode(), data.encode(), hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError("signature")
        parsed = Cursor.model_validate_json(
            base64.b64decode(
                data + "=" * (-len(data) % 4), altchars=b"-_", validate=True
            )
        )
        if parsed.key != key:
            raise ValueError("binding")
        return parsed
    except (ValueError, binascii.Error, ValidationError) as exc:
        raise OperationError("browse_cursor_invalid") from exc


def _fold(session: Session, expression):
    # SQLite lower is ASCII-only. PostgreSQL lower follows database locale, so
    # translate explicitly gives both engines the same Unicode-preserving rule.
    if session.get_bind().dialect.name == "postgresql":
        return func.translate(
            expression, "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz"
        ).collate("C")
    return func.lower(expression).collate("BINARY")


def _visible_collections(session: Session, user: User):
    return rbac.accessible_collection_ids_stmt(session, user)


def _live_folder_scope(entity, visible, user):
    return or_(
        col(entity.collection_id).in_(visible),
        col(entity.collection_id).is_(None) if user.is_superuser else literal(False),
    )


def _multipart_statement(session: Session, user: User, filters: ModelFilters):
    visible = _visible_collections(session, user)
    stmt = select(MultipartModel).where(
        _live_folder_scope(MultipartModel, visible, user)
    )
    if filters.collection:
        path = filters.collection.strip().strip("/").lower()
        folders = select(col(Collection.id)).where(
            live(Collection),
            col(Collection.path) == path
            if filters.direct
            else or_(
                col(Collection.path) == path,
                col(Collection.path).startswith(path + "/"),
            ),
        )
        stmt = stmt.where(col(MultipartModel.collection_id).in_(folders))
    elif filters.direct:
        stmt = stmt.where(col(MultipartModel.collection_id).is_(None))
    for tag in dict.fromkeys(
        value.strip().lower() for value in filters.tag if value.strip()
    ):
        stmt = stmt.where(
            col(MultipartModel.id).in_(
                select(col(MultipartModelTagLink.multipart_model_id))
                .join(Tag, col(Tag.id) == col(MultipartModelTagLink.tag_id))
                .where(col(Tag.slug) == tag, live(Tag))
            )
        )
    if filters.favorites:
        stmt = stmt.where(
            col(MultipartModel.id).in_(
                select(col(MultipartModelStar.multipart_model_id)).where(
                    col(MultipartModelStar.user_id) == user.id
                )
            )
        )
    if filters.q and filters.q.strip():
        # LIKE wildcards are literal input, not a secondary query language.
        needle = filters.q.strip().translate(
            str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")
        )
        pattern = (
            "%"
            + needle.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            + "%"
        )
        stmt = stmt.where(
            or_(
                _fold(session, col(MultipartModel.name)).like(pattern, escape="\\"),
                _fold(session, col(MultipartModel.description)).like(
                    pattern, escape="\\"
                ),
            )
        )
    member_filters = filters.model_copy(
        update={
            "collection": None,
            "direct": False,
            "q": None,
            "tag": [],
            "favorites": False,
        }
    )
    if member_filters != ModelFilters():
        members, _rank = filtered_with_rank(session, user, member_filters)
        members = members.where(_live_folder_scope(Model, visible, user))
        stmt = stmt.where(
            col(MultipartModel.id).in_(
                select(col(MultipartModelChoice.multipart_model_id)).where(
                    or_(
                        col(MultipartModelChoice.source_file_id).is_(None),
                        col(MultipartModelChoice.source_file_id).in_(
                            select(col(File.id)).where(live(File))
                        ),
                    ),
                    col(MultipartModelChoice.model_id).in_(
                        members.with_only_columns(col(Model.id))
                    ),
                )
            )
        )
    return stmt


def _source(session: Session, user: User, query: BrowseQuery):
    filters = query.filters()
    models, rank = filtered_with_rank(session, user, filters)
    models = models.where(
        _live_folder_scope(Model, _visible_collections(session, user), user)
    )
    if query.view is LibraryView.MULTIPART:
        models = models.where(literal(False))
    models, value = _sort_value_and_statement(models, query.sort, rank)
    if query.sort in (ModelSort.NAME_ASC, ModelSort.NAME_DESC):
        value = _fold(session, col(Model.name))
        group_value = _fold(session, col(MultipartModel.name))
    elif query.sort in (ModelSort.DATE_ASC, ModelSort.DATE_DESC) or (
        query.sort is ModelSort.RELEVANCE and rank is None
    ):
        group_value = col(MultipartModel.updated_at)
    else:
        group_value = cast(
            literal(None), value.type if value.type is not None else Float
        )
    model_rows = models.with_only_columns(
        col(Model.id).label("id"),
        literal(BrowseKind.MODEL.value).label("kind"),
        value.label("value"),
    )
    groups = _multipart_statement(session, user, filters).with_only_columns(
        col(MultipartModel.id).label("id"),
        literal(BrowseKind.MULTIPART.value).label("kind"),
        group_value.label("value"),
    )
    return union_all(model_rows, groups).subquery("browse_entries")


def page_items(session: Session, user: User, query: BrowseQuery) -> BrowsePage:
    authenticated_version = user.auth_version
    authority = revision(session)
    browse_revision = authority.browse_revision
    # Refresh authorization inside the authority window instead of relying on
    # the identity map populated by the earlier authentication dependency.
    session.refresh(user)
    if not user.is_active or user.auth_version != authenticated_version:
        raise OperationError("collection_permission_denied", kind=ErrorKind.FORBIDDEN)
    if (
        query.printer_id is not None or query.printer_presence is not None
    ) and not user.is_superuser:
        raise OperationError("admin_required", kind=ErrorKind.FORBIDDEN)
    key = _key(user, query)
    cursor = _decode(query.cursor, key) if query.cursor is not None else None
    if cursor is not None and cursor.revision != browse_revision:
        raise OperationError("browse_refresh_required", kind=ErrorKind.CONFLICT)
    source = _source(session, user, query)
    total = (
        cursor.total
        if cursor is not None
        else session.execute(select(func.count()).select_from(source)).scalar_one()
    )
    descending = query.sort in {
        ModelSort.DATE_DESC,
        ModelSort.NAME_DESC,
        ModelSort.SUCCESS_DESC,
        ModelSort.PRINTED_DESC,
        ModelSort.RELEVANCE,
    }
    order = source.c.value.desc() if descending else source.c.value.asc()
    offset = cursor.offset if cursor is not None else 0
    rows = list(
        session.execute(
            select(source)
            .order_by(
                case((source.c.value.is_(None), 1), else_=0),
                order,
                source.c.kind.asc(),
                source.c.id.asc(),
            )
            .offset(offset)
            .limit(query.limit + 1)
        )
    )
    has_more = len(rows) > query.limit
    rows = rows[: query.limit]
    model_ids = [row.id for row in rows if row.kind == BrowseKind.MODEL.value]
    group_ids = [row.id for row in rows if row.kind == BrowseKind.MULTIPART.value]
    models = {item.id: item for item in read_items_by_ids(session, user, model_ids)}
    groups = {
        item.id: item
        for item in multipart_models.read_items_by_ids(session, user, group_ids)
    }
    if revision(session) != authority:
        raise OperationError("browse_refresh_required", kind=ErrorKind.CONFLICT)
    items = [
        BrowseModel(model=models[row.id])
        if row.kind == BrowseKind.MODEL.value
        else BrowseMultipart(multipart=groups[row.id])
        for row in rows
    ]
    next_cursor = (
        _encode(
            Cursor(
                key=key,
                revision=browse_revision,
                offset=offset + len(rows),
                total=total,
            )
        )
        if has_more
        else None
    )
    return BrowsePage(
        items=items,
        next_cursor=next_cursor,
        total=total,
        browse_revision=browse_revision,
        authorization_revision=authority.authorization_revision,
    )


def thumbnail_items(
    session: Session, user: User, model_ids: list[int]
) -> BrowseThumbnailsRead:
    """Bounded image arrivals without recomputing displayed card membership.

    Ordinary catalog writes may happen during this read. Access changes require
    retiring the response, and clients must also compare this authorization
    revision with the revision of their displayed page before patching URLs.
    """
    if (
        not model_ids
        or len(model_ids) > 24
        or any(model_id <= 0 for model_id in model_ids)
    ):
        raise OperationError("browse_thumbnail_ids_invalid")
    authenticated_version = user.auth_version
    authority = revision(session)
    actor = session.execute(
        select(
            col(User.auth_version), col(User.is_active), col(User.is_superuser)
        ).where(col(User.id) == user.id)
    ).one_or_none()
    if (
        actor is None
        or not actor.is_active
        or actor.auth_version != authenticated_version
    ):
        raise OperationError("collection_permission_denied", kind=ErrorKind.FORBIDDEN)
    set_committed_value(user, "is_superuser", actor.is_superuser)
    ids = list(dict.fromkeys(model_ids))
    rows = session.scalars(
        select(Model)
        .options(lazyload(Model.tags))
        .execution_options(populate_existing=True)
        .where(
            col(Model.id).in_(ids),
            col(Model.id).in_(accessible_live_model_ids_stmt(session, user)),
            _live_folder_scope(Model, _visible_collections(session, user), user),
        )
    ).all()
    models = {model.id: model for model in rows}
    items = [
        BrowseThumbnail(model_id=model_id, thumbnail_url=thumb_url(models[model_id]))
        for model_id in ids
        if model_id in models
    ]
    if revision(session).authorization_revision != authority.authorization_revision:
        raise OperationError("browse_refresh_required", kind=ErrorKind.CONFLICT)
    return BrowseThumbnailsRead(
        items=items, authorization_revision=authority.authorization_revision
    )
