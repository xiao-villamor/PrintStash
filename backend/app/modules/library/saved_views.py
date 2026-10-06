from __future__ import annotations

import json

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.core.time import utcnow
from app.db.models import SavedView
from app.schemas.saved_views import (
    SavedViewCreate,
    SavedViewFilters,
    SavedViewRead,
    SavedViewUpdate,
)


class SavedViewConflict(Exception):
    pass


def uses_retired_filter(raw_filters: str) -> bool:
    """Keep unsupported legacy views stored without broadening their results."""
    filters = json.loads(raw_filters)
    return any(key in filters for key in ("family_id", "family_role", "in_family")) or (
        filters.get("browse") == "families_collapsed"
    )


def _read(row: SavedView) -> SavedViewRead:
    raw = json.loads(row.filters_json)
    # Compatibility is only for stored legacy presentation values. New writes
    # are validated by SavedViewFilters and reject retired mode names.
    if "library_view" not in raw or raw["library_view"] in {"organized", "components"}:
        raw["library_view"] = "all"
    return SavedViewRead(
        id=row.id,
        name=row.name,
        filters=SavedViewFilters.model_validate(raw),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def list_for_user(session: Session, user_id: int) -> list[SavedViewRead]:
    rows = session.exec(
        select(SavedView)
        .where(SavedView.user_id == user_id)
        .order_by(SavedView.name.asc(), SavedView.id.asc())
    ).all()
    return [_read(row) for row in rows if not uses_retired_filter(row.filters_json)]


def get_for_user(session: Session, user_id: int, view_id: int) -> SavedViewRead | None:
    row = session.exec(
        select(SavedView).where(SavedView.id == view_id, SavedView.user_id == user_id)
    ).first()
    return _read(row) if row and not uses_retired_filter(row.filters_json) else None


def create(session: Session, user_id: int, payload: SavedViewCreate) -> SavedViewRead:
    row = SavedView(
        user_id=user_id,
        name=payload.name.strip(),
        filters_json=payload.filters.model_dump_json(exclude_none=True),
    )
    session.add(row)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise SavedViewConflict from exc
    session.refresh(row)
    return _read(row)


def update(
    session: Session, user_id: int, view_id: int, payload: SavedViewUpdate
) -> SavedViewRead | None:
    row = session.exec(
        select(SavedView).where(SavedView.id == view_id, SavedView.user_id == user_id)
    ).first()
    if row is None or uses_retired_filter(row.filters_json):
        return None
    if payload.name is not None:
        row.name = payload.name.strip()
    if payload.filters is not None:
        row.filters_json = payload.filters.model_dump_json(exclude_none=True)
    row.updated_at = utcnow()
    session.add(row)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise SavedViewConflict from exc
    session.refresh(row)
    return _read(row)


def delete(session: Session, user_id: int, view_id: int) -> bool:
    row = session.exec(
        select(SavedView).where(SavedView.id == view_id, SavedView.user_id == user_id)
    ).first()
    if row is None:
        return False
    session.delete(row)
    session.commit()
    return True
